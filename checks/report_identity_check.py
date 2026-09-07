"""Standalone producer checks, run before the existing fast suite by CI."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_live


class ReportIdentity(unittest.TestCase):
    def environment(self):
        return {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_EVENT_NAME": "pull_request",
        }

    def test_ci_and_local_identities(self):
        env = self.environment()
        self.assertEqual(verify_live.run_identity(env), {
            "repository": "owner/repo", "sha": "a" * 40,
            "run_id": "123", "run_attempt": "2", "event_name": "pull_request",
        })
        self.assertIsNone(verify_live.run_identity({}))
        self.assertIsNone(verify_live.run_identity({**env, "GITHUB_ACTIONS": "false"}))

    def test_invalid_identity_rejected_before_request(self):
        env = self.environment()
        for field in env.keys() - {"GITHUB_ACTIONS"}:
            with self.subTest(missing=field):
                bad = dict(env)
                del bad[field]
                with patch.dict(os.environ, bad, clear=True), patch.object(verify_live.live_check, "run_live") as request:
                    with self.assertRaises(ValueError):
                        verify_live.main(["--report", ""])
                    request.assert_not_called()
        for field, value in [
            ("GITHUB_SHA", "head-sha"), ("GITHUB_SHA", "a" * 40 + "\n"),
            ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ID", "123\n"),
            ("GITHUB_RUN_ATTEMPT", "02"), ("GITHUB_RUN_ATTEMPT", 2),
            ("GITHUB_REPOSITORY", "owner/repo/extra"),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                verify_live.run_identity({**env, field: value})

    def test_main_persists_identity_and_preserves_exit_codes(self):
        for code, status in [(0, "ok"), (1, "drift"), (78, "unconfirmed")]:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                report = {"status": status, "exit_code": code, "checks_passed": 1,
                          "checks_run": 1, "drift": [], "unconfirmed_reasons": []}
                path = Path(directory) / "report.json"
                out = io.StringIO()
                with patch.dict(os.environ, self.environment(), clear=True), patch.object(
                    verify_live.live_check, "run_live", return_value=report
                ) as request, contextlib.redirect_stdout(out):
                    self.assertEqual(verify_live.main(["--report", str(path)]), code)
                request.assert_called_once_with(api_key="", root=".")
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(saved["run_identity"], verify_live.run_identity(self.environment()))
                self.assertEqual(saved["exit_code"], code)
                self.assertIn('"run_attempt": "2"', out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
