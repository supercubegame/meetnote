"""Command line entry point. All I/O lives here; core.py stays pure.

Exit codes are part of the contract and are asserted end-to-end:
  0 result produced (status may be "unconfirmed", which is a labelled result)
  2 usage error (bad flags, unreadable or empty input, missing key)
  3 --strict was given and the result is unconfirmed
  4 could not confirm anything: API unreachable, throttled, or auth rejected
  5 contract drift: the endpoint or the response envelope changed
  6 model output could not be turned into our schema at all
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from . import client as client_mod
from . import core
from .output import Emitter, Redactor

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_UNCONFIRMED = 3
EXIT_UNAVAILABLE = 4
EXIT_CONTRACT = 5
EXIT_CONTENT = 6

ENV_KEY = "STEPFUN_API_KEY"
ENV_STUB = "MEETNOTE_STUB_FILE"

# Read-only diagnostic snapshot. Field names may be ADDED, never renamed or
# removed: test_meta.test_diag_field_names_are_stable pins this tuple by
# equality and the e2e tests read these names. See AGENTS.md.
DIAG_FIELDS = (
    "attempts",
    "waits_ms",
    "http_status",
    "endpoint",
    "transport",
    "model",
    "today",
    "contract_paths_checked",
    "retry_budget",
)


def build_parser():
    parser = argparse.ArgumentParser(prog="meetnote", description="会议记录 -> 结构化 JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parse", help="解析一份会议记录文本")
    p.add_argument("path", help="会议记录文件路径，- 表示从 stdin 读")
    p.add_argument("--today", default=None, help="注入当天日期 YYYY-MM-DD，用于可复现的相对日期解析")
    p.add_argument("--model", default=core.MODEL)
    p.add_argument("--endpoint", default=core.ENDPOINT)
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--retry-budget", type=int, default=core.MAX_RETRIES, help="可用重试次数")
    p.add_argument("--backoff-scale", type=float, default=1.0, help="缩放重试等待，调试与测试用")
    p.add_argument("--out", default=None, help="同时把结果写到文件")
    p.add_argument("--log", default=None, help="把运行日志写到文件（已脱敏）")
    p.add_argument("--strict", action="store_true", help="结果为 unconfirmed 时以退出码 3 结束")
    p.add_argument("--diag", action="store_true", help="在结果里带上只读诊断快照")
    p.add_argument("--debug", action="store_true", help="把日志同时打到 stderr")
    return parser


def _read_notes(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def run(argv):
    args = build_parser().parse_args(argv)
    api_key = os.environ.get(ENV_KEY, "")
    redactor = Redactor([api_key])
    emitter = Emitter(redactor, log_path=args.log, debug=args.debug)

    def finish(code):
        emitter.flush_log()
        return code

    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        emitter.err("usage_error: --today 必须是 YYYY-MM-DD")
        return finish(EXIT_USAGE)

    try:
        notes = _read_notes(args.path)
    except OSError as exc:
        emitter.err("usage_error: 读不到输入 (%s)" % type(exc).__name__)
        return finish(EXIT_USAGE)

    try:
        payload = core.build_request(notes, today=today, model=args.model)
    except core.EmptyInputError:
        emitter.err("usage_error: input_empty")
        return finish(EXIT_USAGE)

    stub_path = os.environ.get(ENV_STUB)
    if stub_path:
        # Explicit test-only channel. verify_live.py refuses to run when this is
        # set, so the live gate can never be fed a recording.
        transport = client_mod.StubTransport.from_file(stub_path)
        emitter.log("transport=stub")
    else:
        if not api_key:
            emitter.err("usage_error: 缺少 %s（只走环境变量 / 仓库 secrets）" % ENV_KEY)
            return finish(EXIT_USAGE)
        transport = client_mod.UrllibTransport(timeout=args.timeout)
        emitter.log("transport=urllib")

    api = client_mod.Client(
        transport,
        api_key,
        emitter=emitter,
        endpoint=args.endpoint,
        retry_budget=args.retry_budget,
        backoff_scale=args.backoff_scale,
    )

    def diag():
        return {
            "attempts": api.attempts,
            "waits_ms": list(api.waits_ms),
            "http_status": api.last_status,
            "endpoint": api.endpoint,
            "transport": type(transport).__name__,
            "model": args.model,
            "today": today.isoformat(),
            "contract_paths_checked": len(core.REQUIRED_RESPONSE_PATHS),
            "retry_budget": api.retry_budget,
        }

    labels = {
        EXIT_UNAVAILABLE: "unconfirmed",
        EXIT_CONTRACT: "contract_drift",
        EXIT_CONTENT: "content_error",
    }

    def fail(code, reason, detail=None):
        emitter.err("%s: %s" % (labels.get(code, "error"), reason))
        if detail:
            emitter.err("detail: %s" % detail)
        emitter.log("exit=%d reason=%s detail=%s" % (code, reason, detail))
        if args.diag:
            emitter.err("diag: %s" % json.dumps(diag(), ensure_ascii=False, sort_keys=True))
        return finish(code)

    try:
        status, raw = api.complete(payload)
    except client_mod.AuthError as exc:
        return fail(EXIT_UNAVAILABLE, "auth_rejected:" + exc.reason, exc.detail)
    except client_mod.ContractError as exc:
        return fail(EXIT_CONTRACT, exc.reason, exc.detail)
    except client_mod.AvailabilityError as exc:
        return fail(EXIT_UNAVAILABLE, exc.reason, exc.detail)

    try:
        envelope = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return fail(EXIT_CONTRACT, "envelope_not_json", raw[:200].decode("utf-8", "replace"))

    problems = core.check_envelope(envelope)
    if problems:
        return fail(EXIT_CONTRACT, "envelope_violations", ",".join(problems))

    choice = envelope["choices"][0]
    content = choice["message"]["content"]
    finish_reason = choice.get("finish_reason", "stop")

    try:
        result = core.parse_content(content, today=today, finish_reason=finish_reason)
    except core.ContentError as exc:
        return fail(EXIT_CONTENT, str(exc), content[:200] if isinstance(content, str) else None)

    result["meta"] = {
        "model": envelope.get("model"),
        "response_id": envelope.get("id"),
        "today": today.isoformat(),
        "http_status": status,
    }
    if args.diag:
        result["_diag"] = diag()

    emitter.out_json(result)
    if args.out:
        emitter.dump_json(args.out, result)
    emitter.log("done status=%s counts=%s" % (result["status"], result["counts"]))

    if args.strict and result["status"] != "ok":
        return finish(EXIT_UNCONFIRMED)
    return finish(EXIT_OK)


def main():
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
