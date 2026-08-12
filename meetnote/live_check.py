"""The live gate: the only place real response drift can be discovered.

Recorded fixtures always agree with our own expectations, so the fast gate is
structurally incapable of noticing that StepFun changed its response shape.
This module exists to notice, and it is built so that it cannot quietly decay
into replaying our own recordings.

Two outcomes are deliberately kept apart:

  unconfirmed (exit 78)  We learned nothing. Secret absent, API unreachable,
                         throttled, or the credential was rejected. Never
                         reported as a pass.
  drift (exit 1)         We did talk to it and the deal changed: a 4xx that
                         rejects our request shape, or a 2xx whose body
                         violates core.REQUIRED_RESPONSE_PATHS. Also raised
                         when a replay guard fails, because a gate that is not
                         measuring reality is worse than no gate.

Additive envelope changes (new keys we do not read) are reported, not fatal.

tests/test_live_gate.py drives every one of these branches offline with an
injected transport, so each outcome is known to be reachable. A drift detector
that has never been observed failing is indistinguishable from a decoration.
"""
from __future__ import annotations

import glob
import json
import os
import time
import uuid
from datetime import date

from . import client as client_mod
from . import core

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_UNCONFIRMED = 78

FIXTURE_ID_PREFIX = "fixture-"
FIXTURE_GLOB = "tests/fixtures/*.json"

# Frozen by equality in test_meta. Emptying this set would make the live gate
# pass for free, so the fast gate refuses to let that happen quietly.
EXPECTED_CONTRACT_PATH_COUNT = 7

KNOWN_TOP_LEVEL_KEYS = {"id", "model", "choices", "usage", "object", "created"}

PROBE_NOTES = (
    "会议纪要（自动化探针 {nonce}）\n"
    "时间：{today}\n"
    "参会：赵岩（增长）、{nonce}（探针账号）\n"
    "讨论：本周的导入失败率偏高，决定先回滚上周的批量校验改动。\n"
    "赵岩负责在 8月20日 之前给出失败样本分析。\n"
    "{nonce} 负责在 下周五 之前补一版回归用例。\n"
)


def fixture_response_ids(root="."):
    """Every id we have ever recorded. A live response must not be one of them."""
    ids = set()
    for path in sorted(glob.glob(os.path.join(root, FIXTURE_GLOB))):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for step in data.get("responses", []):
            body = step.get("body_json") or {}
            if isinstance(body, dict) and isinstance(body.get("id"), str):
                ids.add(body["id"])
    return ids


class Report:
    def __init__(self):
        self.checks = []
        self.drift = []
        self.unconfirmed_reasons = []
        self.evidence = {}

    def check(self, name, ok, detail="", fatal=True):
        self.checks.append({"id": name, "ok": bool(ok), "detail": detail})
        if not ok:
            if fatal:
                self.drift.append("%s: %s" % (name, detail))
            else:
                self.unconfirmed_reasons.append("%s: %s" % (name, detail))
        return bool(ok)

    def note_unconfirmed(self, reason):
        if reason not in self.unconfirmed_reasons:
            self.unconfirmed_reasons.append(reason)

    def as_dict(self):
        if self.drift:
            status, code = "drift", EXIT_DRIFT
        elif self.unconfirmed_reasons:
            status, code = "unconfirmed", EXIT_UNCONFIRMED
        else:
            status, code = "ok", EXIT_OK
        passed = len([c for c in self.checks if c["ok"]])
        return {
            "gate": "live",
            "status": status,
            "exit_code": code,
            "checks_run": len(self.checks),
            "checks_passed": passed,
            "drift": self.drift,
            "unconfirmed_reasons": self.unconfirmed_reasons,
            "evidence": self.evidence,
            "checks": self.checks,
        }


def scan_repo_for(value, root="."):
    """Return the files that contain `value`. Only meaningful with the real key,
    which is why this scan lives in the live gate and not the fast one."""
    hits = []
    if not value or len(value) < 8:
        return hits
    skip_dirs = {".git", "__pycache__", "reports", "incoming", ".venv"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    if value in fh.read():
                        hits.append(path)
            except OSError:
                continue
    return hits


def run_live(
    *,
    api_key,
    root=".",
    transport=None,
    today=None,
    nonce=None,
    endpoint=core.ENDPOINT,
    clock=None,
    retry_budget=None,
):
    report = Report()
    today = today or date.today()
    nonce = nonce or ("probe-" + uuid.uuid4().hex[:12])
    report.evidence["nonce"] = nonce
    report.evidence["endpoint"] = endpoint
    report.evidence["today"] = today.isoformat()

    # --- replay guards: prove this gate is looking at reality ---------------
    report.check(
        "stub_channel_absent",
        not os.environ.get("MEETNOTE_STUB_FILE"),
        "MEETNOTE_STUB_FILE is set; the live gate must never read a recording",
    )
    report.check(
        "contract_not_emptied",
        len(core.REQUIRED_RESPONSE_PATHS) == EXPECTED_CONTRACT_PATH_COUNT,
        "expected %d required paths, found %d"
        % (EXPECTED_CONTRACT_PATH_COUNT, len(core.REQUIRED_RESPONSE_PATHS)),
    )
    report.check(
        "endpoint_is_real",
        endpoint == core.ENDPOINT and endpoint.startswith("https://"),
        "endpoint=%s" % endpoint,
    )

    if not api_key:
        report.note_unconfirmed("secret_absent: STEPFUN_API_KEY 不可用，本次没有验证任何真实响应")
        report.evidence["attempted_request"] = False
        return report.as_dict()

    transport = transport or client_mod.UrllibTransport(timeout=90)
    report.evidence["transport"] = type(transport).__name__
    report.check(
        "transport_is_real",
        isinstance(transport, client_mod.UrllibTransport),
        "transport=%s is not a real http transport" % type(transport).__name__,
    )

    leaks = scan_repo_for(api_key, root)
    report.check("key_absent_from_repo", not leaks, "key found in: %s" % ",".join(leaks))

    payload = core.build_request(PROBE_NOTES.format(nonce=nonce, today=today.isoformat()), today=today)
    api = client_mod.Client(
        transport,
        api_key,
        endpoint=endpoint,
        clock=clock,
        retry_budget=retry_budget,
    )
    report.evidence["attempted_request"] = True

    started = time.monotonic()
    try:
        status, raw = api.complete(payload)
    except client_mod.AuthError as exc:
        report.evidence["attempts"] = api.attempts
        report.note_unconfirmed("auth_rejected:%s" % exc.reason)
        return report.as_dict()
    except client_mod.ContractError as exc:
        report.evidence["attempts"] = api.attempts
        report.check("request_contract_accepted", False, "%s %s" % (exc.reason, (exc.detail or "")[:200]))
        return report.as_dict()
    except client_mod.AvailabilityError as exc:
        report.evidence["attempts"] = api.attempts
        report.note_unconfirmed("api_unavailable:%s (%s)" % (exc.reason, (exc.detail or "")[:120]))
        return report.as_dict()
    finally:
        # Independent measurement, reported as evidence rather than asserted:
        # CI machines are fast and a threshold here would be decoration.
        report.evidence["elapsed_ms"] = int((time.monotonic() - started) * 1000)

    report.check("request_contract_accepted", True, "http %d" % status)
    report.evidence["attempts"] = api.attempts
    report.evidence["http_status"] = status
    report.evidence["response_bytes"] = len(raw)

    try:
        envelope = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        report.check("envelope_is_json", False, raw[:200].decode("utf-8", "replace"))
        return report.as_dict()
    report.check("envelope_is_json", True, "%d bytes" % len(raw))

    problems = core.check_envelope(envelope)
    report.check("envelope_matches_contract", not problems, ",".join(problems))

    response_id = envelope.get("id") if isinstance(envelope, dict) else None
    recorded = fixture_response_ids(root)
    report.evidence["response_id"] = response_id
    report.evidence["fixture_ids_known"] = len(recorded)
    report.check(
        "response_is_not_a_recording",
        isinstance(response_id, str)
        and bool(response_id)
        and not response_id.startswith(FIXTURE_ID_PREFIX)
        and response_id not in recorded,
        "response id %r collides with recorded fixtures" % (response_id,),
    )

    if isinstance(envelope, dict):
        additions = sorted(k for k in envelope if k not in KNOWN_TOP_LEVEL_KEYS)
        report.evidence["envelope_additions"] = additions
        if additions:
            report.checks.append(
                {"id": "envelope_additions_are_informational", "ok": True, "detail": ",".join(additions)}
            )

    if problems:
        return report.as_dict()

    content = envelope["choices"][0]["message"]["content"]
    finish_reason = envelope["choices"][0].get("finish_reason", "stop")
    report.evidence["finish_reason"] = finish_reason
    report.evidence["content_bytes"] = len(content)

    try:
        result = core.parse_content(content, today=today, finish_reason=finish_reason)
    except core.ContentError as exc:
        report.check("content_matches_schema", False, "%s | head=%s" % (exc, content[:200]))
        return report.as_dict()
    report.check("content_matches_schema", True, "status=%s" % result["status"])
    report.evidence["counts"] = result["counts"]
    report.evidence["result_status"] = result["status"]
    report.evidence["unconfirmed_reasons_in_result"] = result["unconfirmed_reasons"]

    # Liveness proof from the semantic side. Soft on purpose: a model is allowed
    # to paraphrase, and turning this red would make the gate flaky. Reported
    # loudly instead, and it cannot be satisfied by any recording.
    report.check(
        "nonce_echoed_by_model",
        nonce in content,
        "probe nonce %s absent from model output; structural drift checks are still authoritative" % nonce,
        fatal=False,
    )

    report.check(
        "probe_yielded_extractions",
        result["counts"]["participants"] > 0 and result["counts"]["action_items"] > 0,
        "probe has 2 participants and 2 action items but got %s" % result["counts"],
        fatal=False,
    )

    report.evidence["due_dates"] = [item["due_date"] for item in result["action_items"]]
    return report.as_dict()
