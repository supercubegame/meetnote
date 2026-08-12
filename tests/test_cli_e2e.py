"""End to end through the real CLI in a subprocess, with a canned transport.

What makes the secret-hygiene checks here non-vacuous: the sentinel is a random
value generated per run (so it can never be committed), it really is handed to
the process as STEPFUN_API_KEY, and one fixture simulates the API echoing our
Authorization header back inside an error body. That is the realistic leak path.
A negative twin proves the detector is capable of failing.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import uuid

from meetnote import cli as cli_mod
from meetnote import client as client_mod
from meetnote import output

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
NOTES = FIXTURES / "notes_zh.txt"
TODAY = "2026-08-12"


def sentinel():
    return "sk-live-" + uuid.uuid4().hex


def run_cli(args, stub=None, key="", stdin=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["STEPFUN_API_KEY"] = key
    env.pop("MEETNOTE_STUB_FILE", None)
    if stub is not None:
        env["MEETNOTE_STUB_FILE"] = str(stub)
    return subprocess.run(
        [sys.executable, "-m", "meetnote.cli"] + list(args),
        cwd=str(REPO_ROOT),
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=120,
    )


def base_args(*extra):
    return ["parse", str(NOTES), "--today", TODAY] + list(extra)


class HappyPath(unittest.TestCase):
    def test_happy_path_stdout_json(self):
        proc = run_cli(base_args(), stub=FIXTURES / "ok.json", key=sentinel())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["counts"], {"participants": 2, "decisions": 1, "action_items": 2})
        self.assertEqual([p["name"] for p in result["participants"]], ["陈迪", "林岚"])
        self.assertEqual(result["decisions"][0]["text"], "下一版本先做导入，不做导出")
        first, second = result["action_items"]
        self.assertEqual((first["owner"], first["due_date"], first["due_date_raw"]), ("陈迪", "2026-08-20", "8月20日"))
        self.assertEqual((second["owner"], second["due_date"], second["due_date_raw"]), ("林岚", "2026-08-21", "下周五"))
        self.assertEqual(result["meta"]["response_id"], "fixture-ok-001")

    def test_out_file_matches_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "result.json"
            proc = run_cli(base_args("--out", str(out)), stub=FIXTURES / "ok.json", key=sentinel())
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertGreater(out.stat().st_size, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), json.loads(proc.stdout))

    def test_diag_snapshot_fields(self):
        proc = run_cli(base_args("--diag"), stub=FIXTURES / "ok.json", key=sentinel())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        diag = json.loads(proc.stdout)["_diag"]
        self.assertEqual(set(diag), set(cli_mod.DIAG_FIELDS))
        self.assertEqual(diag["attempts"], 1)
        self.assertEqual(diag["transport"], "StubTransport")
        self.assertEqual(diag["contract_paths_checked"], 7)
        self.assertEqual(diag["retry_budget"], 5)
        self.assertEqual(diag["http_status"], 200)

    def test_stdin_input(self):
        proc = run_cli(
            ["parse", "-", "--today", TODAY],
            stub=FIXTURES / "ok.json",
            key=sentinel(),
            stdin=NOTES.read_text(encoding="utf-8"),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "ok")

    def test_empty_input_usage_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            blank = pathlib.Path(tmp) / "blank.txt"
            blank.write_text("\n   \n\t\n", encoding="utf-8")
            proc = run_cli(["parse", str(blank), "--today", TODAY], stub=FIXTURES / "ok.json", key=sentinel())
        self.assertEqual(proc.returncode, 2)
        self.assertIn("input_empty", proc.stderr)
        self.assertEqual(proc.stdout, "")


class SecretHygiene(unittest.TestCase):
    def test_sentinel_absent_from_every_sink(self):
        key = sentinel()
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "result.json"
            log = pathlib.Path(tmp) / "run.log"
            proc = run_cli(
                base_args("--out", str(out), "--log", str(log), "--diag", "--debug"),
                stub=FIXTURES / "ok.json",
                key=key,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            # Non-vacuity: this key really is what goes on the wire.
            authorization = client_mod.Client(client_mod.StubTransport([]), key).build_headers()["Authorization"]
            self.assertIn(key, authorization)
            log_text = log.read_text(encoding="utf-8")
            self.assertGreater(len(log_text.strip()), 0)
            sinks = {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "out_file": out.read_text(encoding="utf-8"),
                "log_file": log_text,
            }
        for name, blob in sinks.items():
            self.assertNotIn(key, blob, "key leaked into %s" % name)
            self.assertNotIn("Bearer ", blob, "authorization header leaked into %s" % name)

    def test_error_body_echoing_key_is_masked(self):
        key = sentinel()
        script = {
            "responses": [
                {
                    "status": 400,
                    "body": json.dumps(
                        {"error": {"message": "invalid Authorization header: Bearer " + key}},
                        ensure_ascii=False,
                    ),
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            stub_path = pathlib.Path(tmp) / "echo.json"
            stub_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
            log = pathlib.Path(tmp) / "run.log"
            proc = run_cli(base_args("--log", str(log)), stub=stub_path, key=key)
            log_text = log.read_text(encoding="utf-8")
        self.assertEqual(proc.returncode, 5, proc.stderr)
        self.assertNotIn(key, proc.stderr)
        self.assertNotIn(key, log_text)
        self.assertIn(output.MASK, proc.stderr)

    def test_leak_detector_would_catch_an_unredacted_sink(self):
        key = sentinel()
        text = "Authorization: Bearer " + key
        # Negative twin: with no secrets registered the sentinel does show up, so
        # the assertions above are capable of failing.
        self.assertIn(key, output.Redactor([]).scrub(text))
        scrubbed = output.Redactor([key]).scrub(text)
        self.assertNotIn(key, scrubbed)
        self.assertIn(output.MASK, scrubbed)


class FailureModes(unittest.TestCase):
    def _diag_from_stderr(self, stderr):
        self.assertIn("diag: ", stderr)
        return json.loads(stderr.split("diag: ", 1)[1].splitlines()[0])

    def test_unavailable_exit_code_and_full_retry_ladder(self):
        proc = run_cli(
            base_args("--diag", "--backoff-scale", "0.01"),
            stub=FIXTURES / "unavailable.json",
            key=sentinel(),
        )
        self.assertEqual(proc.returncode, 4)
        self.assertIn("unconfirmed: api_unreachable", proc.stderr)
        diag = self._diag_from_stderr(proc.stderr)
        self.assertEqual(diag["attempts"], 6)
        self.assertEqual(len(diag["waits_ms"]), 5)

    def test_contract_exit_code_on_400(self):
        proc = run_cli(base_args("--diag"), stub=FIXTURES / "http400.json", key=sentinel())
        self.assertEqual(proc.returncode, 5)
        self.assertIn("contract_drift: http_400", proc.stderr)
        self.assertEqual(self._diag_from_stderr(proc.stderr)["attempts"], 1)

    def test_envelope_drift_exit_code(self):
        proc = run_cli(base_args(), stub=FIXTURES / "envelope_drift.json", key=sentinel())
        self.assertEqual(proc.returncode, 5)
        self.assertIn("envelope_violations", proc.stderr)
        self.assertIn("usage.completion_tokens:missing", proc.stderr)

    def test_unconfirmed_result_still_exits_zero(self):
        proc = run_cli(base_args(), stub=FIXTURES / "unconfirmed.json", key=sentinel())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertEqual(result["status"], "unconfirmed")
        for reason in ("decision_text_missing", "owner_missing", "due_unparsed", "due_invalid_calendar_date"):
            self.assertIn(reason, result["unconfirmed_reasons"])
        self.assertEqual(result["counts"], {"participants": 1, "decisions": 2, "action_items": 2})

    def test_strict_exit_code_on_unconfirmed(self):
        proc = run_cli(base_args("--strict"), stub=FIXTURES / "unconfirmed.json", key=sentinel())
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["status"], "unconfirmed")

    def test_truncated_is_flagged_unconfirmed(self):
        proc = run_cli(base_args(), stub=FIXTURES / "truncated.json", key=sentinel())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertIn("truncated:finish_reason=length", result["unconfirmed_reasons"])

    def test_content_error_exit_code(self):
        proc = run_cli(base_args(), stub=FIXTURES / "badcontent.json", key=sentinel())
        self.assertEqual(proc.returncode, 6)
        self.assertIn("content_error: content_not_json", proc.stderr)

    def test_missing_key_without_stub_is_usage_error(self):
        proc = run_cli(base_args(), stub=None, key="")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("STEPFUN_API_KEY", proc.stderr)


if __name__ == "__main__":
    unittest.main()
