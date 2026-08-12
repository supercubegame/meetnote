"""Transport behaviour. No network: either a stub or a patched urlopen.

The whole point of these checks is the taxonomy. "The API is down" and "the API
changed" must never collapse into one outcome, because one of them is allowed to
leave us uncertain and the other one has to be fatal.
"""
import json
import unittest

from meetnote import client, core

OK_BODY = {
    "id": "fixture-inline-001",
    "model": core.MODEL,
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
}


def stub(*steps):
    return client.StubTransport(list(steps))


def make_client(transport, key="key-abcdefgh12345678", **kwargs):
    kwargs.setdefault("clock", client.FakeClock())
    return client.Client(transport, key, **kwargs)


class Classification(unittest.TestCase):
    def test_2xx_returns_none(self):
        for status in (200, 201, 204, 299):
            self.assertIsNone(client.classify_status(status, b"{}"))

    def test_auth_statuses_are_auth_errors(self):
        for status in client.AUTH_STATUSES:
            with self.assertRaises(client.AuthError) as ctx:
                client.classify_status(status, b"nope")
            self.assertEqual(ctx.exception.kind, "auth")
            self.assertIsInstance(ctx.exception, client.AvailabilityError)

    def test_retryable_statuses_are_availability(self):
        for status in client.RETRYABLE_STATUSES:
            with self.assertRaises(client.AvailabilityError) as ctx:
                client.classify_status(status, b"busy")
            self.assertEqual(ctx.exception.kind, "availability")
            self.assertNotIsInstance(ctx.exception, client.AuthError)

    def test_client_4xx_is_contract_error(self):
        for status in (400, 404, 415, 422):
            with self.assertRaises(client.ContractError) as ctx:
                client.classify_status(status, b"unknown parameter")
            self.assertEqual(ctx.exception.kind, "contract")
            self.assertIn("unknown parameter", ctx.exception.detail)


class Backoff(unittest.TestCase):
    def test_backoff_cap_is_reachable(self):
        self.assertEqual(client.backoff_ms(core.MAX_RETRIES), core.BACKOFF_CAP_MS)
        self.assertLess(client.backoff_ms(core.MAX_RETRIES - 1), core.BACKOFF_CAP_MS)

    def test_backoff_sequence_and_scale(self):
        self.assertEqual([client.backoff_ms(n) for n in range(1, 7)], [500, 1000, 2000, 4000, 8000, 8000])
        self.assertEqual(client.backoff_ms(1, 0.01), 5)


class Retrying(unittest.TestCase):
    def test_retries_then_succeeds(self):
        api = make_client(stub({"status": 503, "body": "busy"}, {"status": 200, "body_json": OK_BODY}))
        status, raw = api.complete({"model": core.MODEL})
        self.assertEqual(status, 200)
        self.assertEqual(api.attempts, 2)
        self.assertEqual(api.waits_ms, [500])
        self.assertEqual(json.loads(raw)["id"], "fixture-inline-001")

    def test_gives_up_after_retry_budget(self):
        api = make_client(stub({"status": 503, "body": "busy"}))
        with self.assertRaises(client.AvailabilityError) as ctx:
            api.complete({"model": core.MODEL})
        self.assertEqual(ctx.exception.reason, "api_unreachable")
        self.assertEqual(api.attempts, core.MAX_RETRIES + 1)
        self.assertEqual(api.waits_ms, [500, 1000, 2000, 4000, 8000])
        self.assertIn("http_503", ctx.exception.detail)

    def test_no_retry_on_contract(self):
        api = make_client(stub({"status": 400, "body": "unknown parameter: response_format"}))
        with self.assertRaises(client.ContractError):
            api.complete({"model": core.MODEL})
        self.assertEqual(api.attempts, 1)
        self.assertEqual(api.waits_ms, [])

    def test_no_retry_on_auth(self):
        api = make_client(stub({"status": 401, "body": "bad key"}))
        with self.assertRaises(client.AuthError):
            api.complete({"model": core.MODEL})
        self.assertEqual(api.attempts, 1)

    def test_lower_retry_budget_is_respected(self):
        api = make_client(stub({"status": 503, "body": "busy"}), retry_budget=1)
        with self.assertRaises(client.AvailabilityError):
            api.complete({"model": core.MODEL})
        self.assertEqual(api.attempts, 2)
        self.assertEqual(api.waits_ms, [500])

    def test_transport_exception_is_availability(self):
        transport = client.UrllibTransport(timeout=1)
        original = client.urllib.request.urlopen

        def boom(*args, **kwargs):
            raise client.urllib.error.URLError("no route to host")

        client.urllib.request.urlopen = boom
        try:
            with self.assertRaises(client.AvailabilityError) as ctx:
                transport.post("https://example.invalid/x", {}, b"{}")
        finally:
            client.urllib.request.urlopen = original
        self.assertEqual(ctx.exception.reason, "transport_URLError")


class KeyHandling(unittest.TestCase):
    def test_headers_carry_key_once(self):
        api = make_client(stub({"status": 200, "body_json": OK_BODY}), key="key-abcdefgh12345678")
        headers = api.build_headers()
        self.assertEqual(headers["Authorization"], "Bearer key-abcdefgh12345678")
        self.assertEqual(sorted(headers), ["Authorization", "Content-Type"])

    def test_request_body_never_contains_key(self):
        transport = stub({"status": 200, "body_json": OK_BODY})
        api = make_client(transport, key="key-abcdefgh12345678")
        api.complete(core.build_request("张三：下周五交", today=__import__("datetime").date(2026, 8, 12)))
        body = transport.calls[0]["body"].decode("utf-8")
        self.assertNotIn("key-abcdefgh12345678", body)
        self.assertIn("Bearer key-abcdefgh12345678", transport.calls[0]["headers"]["Authorization"])

    def test_stub_transport_records_calls(self):
        transport = stub({"status": 200, "body_json": OK_BODY})
        api = make_client(transport, endpoint=core.ENDPOINT)
        api.complete({"model": core.MODEL})
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0]["url"], core.ENDPOINT)


if __name__ == "__main__":
    unittest.main()
