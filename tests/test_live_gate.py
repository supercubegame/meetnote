"""The live gate's own failure modes, driven offline through an injected transport.

Without these, every replay guard and every drift branch would be a check that
has never once been seen failing, which is the same shape as a check that cannot
fail. Each test here forces one branch and asserts which of the three outcomes it
produces: pass, unconfirmed, or red.

The transport double subclasses the real UrllibTransport on purpose. The
`transport_is_real` guard exists to catch StubTransport (a recording player), and
there is a dedicated test proving it does exactly that.
"""
import json
import os
import pathlib
import unittest
from datetime import date

from meetnote import client as client_mod
from meetnote import live_check

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
TODAY = date(2026, 8, 12)
NONCE = "probe-abc123def456"
# Long enough for the redactor and the repo scan to take it seriously, and it is
# deliberately not a string that appears anywhere in the repository.
KEY = "sk-live-0000000000000000000000000000"


def content_for(nonce):
    return json.dumps(
        {
            "participants": [{"name": "赵岩", "role": "增长"}, {"name": nonce, "role": "探针账号"}],
            "decisions": ["先回滚上周的批量校验改动"],
            "action_items": [
                {"task": "给出失败样本分析", "owner": "赵岩", "due_date": "8月20日"},
                {"task": "补一版回归用例", "owner": nonce, "due_date": "下周五"},
            ],
        },
        ensure_ascii=False,
    )


def envelope_for(nonce, response_id="chatcmpl-live-001", drop_usage=False):
    body = {
        "id": response_id,
        "object": "chat.completion",
        "created": 1786000000,
        "model": "step-3.7-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content_for(nonce)},
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 60},
    }
    if drop_usage:
        del body["usage"]["completion_tokens"]
    return json.dumps(body, ensure_ascii=False)


class FakeUrllib(client_mod.UrllibTransport):
    """A real http transport with its one network call replaced."""

    def __init__(self, status=200, body="{}"):
        super().__init__(timeout=1)
        self.status = status
        self.body = body
        self.calls = 0

    def post(self, url, headers, body):
        self.calls += 1
        return self.status, self.body.encode("utf-8")


def run(**kwargs):
    kwargs.setdefault("api_key", KEY)
    kwargs.setdefault("root", ROOT)
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("nonce", NONCE)
    kwargs.setdefault("clock", client_mod.FakeClock())
    return live_check.run_live(**kwargs)


class Passing(unittest.TestCase):
    def test_live_like_response_passes(self):
        report = run(transport=FakeUrllib(200, envelope_for(NONCE)))
        self.assertEqual(report["status"], "ok", report["drift"] + report["unconfirmed_reasons"])
        self.assertEqual(report["exit_code"], live_check.EXIT_OK)
        self.assertEqual(report["drift"], [])
        self.assertEqual(report["unconfirmed_reasons"], [])
        self.assertEqual(report["checks_run"], report["checks_passed"])
        self.assertEqual(report["checks_run"], 12)
        self.assertEqual(report["evidence"]["counts"], {"participants": 2, "decisions": 1, "action_items": 2})
        self.assertEqual(report["evidence"]["due_dates"], ["2026-08-20", "2026-08-21"])

    def test_additive_envelope_change_is_not_drift(self):
        body = json.loads(envelope_for(NONCE))
        body["system_fingerprint"] = "fp_new_field"
        report = run(transport=FakeUrllib(200, json.dumps(body, ensure_ascii=False)))
        self.assertEqual(report["status"], "ok", report["drift"])
        self.assertEqual(report["evidence"]["envelope_additions"], ["system_fingerprint"])
        self.assertEqual(report["checks_run"], 13)


class Unconfirmed(unittest.TestCase):
    def test_missing_secret_is_unconfirmed_never_a_pass(self):
        report = run(api_key="")
        self.assertEqual(report["status"], "unconfirmed")
        self.assertEqual(report["exit_code"], live_check.EXIT_UNCONFIRMED)
        self.assertEqual(report["evidence"]["attempted_request"], False)
        self.assertEqual(report["drift"], [])
        self.assertTrue(any("secret_absent" in r for r in report["unconfirmed_reasons"]))

    def test_outage_is_unconfirmed_not_drift(self):
        transport = FakeUrllib(503, '{"error": "upstream unavailable"}')
        report = run(transport=transport)
        self.assertEqual(report["status"], "unconfirmed")
        self.assertEqual(report["exit_code"], live_check.EXIT_UNCONFIRMED)
        self.assertEqual(report["drift"], [])
        self.assertTrue(any("api_unavailable" in r for r in report["unconfirmed_reasons"]))
        self.assertEqual(transport.calls, 6)

    def test_auth_rejection_is_unconfirmed_not_drift(self):
        report = run(transport=FakeUrllib(401, '{"error": "invalid api key"}'))
        self.assertEqual(report["status"], "unconfirmed")
        self.assertEqual(report["drift"], [])
        self.assertTrue(any("auth_rejected" in r for r in report["unconfirmed_reasons"]))
        self.assertEqual(report["evidence"]["attempts"], 1)

    def test_absent_nonce_is_soft_and_never_red(self):
        report = run(transport=FakeUrllib(200, envelope_for("some-other-token")))
        self.assertEqual(report["status"], "unconfirmed")
        self.assertEqual(report["exit_code"], live_check.EXIT_UNCONFIRMED)
        self.assertEqual(report["drift"], [])
        self.assertTrue(any("nonce_echoed_by_model" in r for r in report["unconfirmed_reasons"]))


class Red(unittest.TestCase):
    def test_rejected_request_shape_is_drift(self):
        transport = FakeUrllib(400, '{"error": {"message": "unknown parameter: response_format"}}')
        report = run(transport=transport)
        self.assertEqual(report["status"], "drift")
        self.assertEqual(report["exit_code"], live_check.EXIT_DRIFT)
        self.assertTrue(any("request_contract_accepted" in d for d in report["drift"]))
        self.assertEqual(transport.calls, 1)

    def test_envelope_drift_is_red(self):
        report = run(transport=FakeUrllib(200, envelope_for(NONCE, drop_usage=True)))
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("usage.completion_tokens:missing" in d for d in report["drift"]))

    def test_non_json_envelope_is_red(self):
        report = run(transport=FakeUrllib(200, "<html>gateway</html>"))
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("envelope_is_json" in d for d in report["drift"]))

    def test_prose_content_is_red(self):
        body = json.loads(envelope_for(NONCE))
        body["choices"][0]["message"]["content"] = "当然可以！这次会议讨论了导入失败率。"
        report = run(transport=FakeUrllib(200, json.dumps(body, ensure_ascii=False)))
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("content_matches_schema" in d for d in report["drift"]))

    def test_recorded_response_id_is_red(self):
        report = run(transport=FakeUrllib(200, envelope_for(NONCE, response_id="fixture-ok-001")))
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("response_is_not_a_recording" in d for d in report["drift"]))

    def test_stub_transport_fails_the_replay_guard(self):
        transport = client_mod.StubTransport([{"status": 200, "body": envelope_for(NONCE)}])
        report = run(transport=transport)
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("transport_is_real" in d for d in report["drift"]))

    def test_stub_env_channel_is_red(self):
        previous = os.environ.get("MEETNOTE_STUB_FILE")
        os.environ["MEETNOTE_STUB_FILE"] = "tests/fixtures/ok.json"
        try:
            report = run(transport=FakeUrllib(200, envelope_for(NONCE)))
        finally:
            if previous is None:
                os.environ.pop("MEETNOTE_STUB_FILE", None)
            else:
                os.environ["MEETNOTE_STUB_FILE"] = previous
        self.assertEqual(report["status"], "drift")
        self.assertEqual(len(report["drift"]), 1)
        self.assertIn("stub_channel_absent", report["drift"][0])

    def test_emptied_contract_is_red(self):
        original = live_check.EXPECTED_CONTRACT_PATH_COUNT
        live_check.EXPECTED_CONTRACT_PATH_COUNT = original + 1
        try:
            report = run(transport=FakeUrllib(200, envelope_for(NONCE)))
        finally:
            live_check.EXPECTED_CONTRACT_PATH_COUNT = original
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("contract_not_emptied" in d for d in report["drift"]))

    def test_key_present_in_repo_is_red(self):
        # "step-3.7-flash" really is in the source tree, so this proves the scan
        # can find something rather than always coming back clean.
        report = run(api_key="step-3.7-flash", transport=FakeUrllib(200, envelope_for(NONCE)))
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("key_absent_from_repo" in d for d in report["drift"]))

    def test_fake_endpoint_is_red(self):
        report = run(transport=FakeUrllib(200, envelope_for(NONCE)), endpoint="http://localhost:8080/v1/chat")
        self.assertEqual(report["status"], "drift")
        self.assertTrue(any("endpoint_is_real" in d for d in report["drift"]))


if __name__ == "__main__":
    unittest.main()
