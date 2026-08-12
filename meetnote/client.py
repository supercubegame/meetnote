"""Transport layer. Injectable on purpose so the fast gate needs no network.

Failure taxonomy. This is the whole point of the module:

  AvailabilityError  the API is down, throttled or unreachable. We learned
                     nothing, so the result is marked unconfirmed. Retryable.
  AuthError          subclass of Availability. Still cannot confirm anything,
                     but the cause is our credential, not their uptime. Not
                     retryable, and reported with its own reason string.
  ContractError      we did talk to it and the deal changed. Must go red.
                     Covers 4xx that reject our request shape and any 2xx whose
                     body violates core.REQUIRED_RESPONSE_PATHS.
"""
from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request

from . import core


class TransportError(Exception):
    kind = "unknown"

    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class AvailabilityError(TransportError):
    kind = "availability"


class AuthError(AvailabilityError):
    kind = "auth"


class ContractError(TransportError):
    kind = "contract"


RETRYABLE_STATUSES = (408, 409, 425, 429, 500, 502, 503, 504, 522, 524)
AUTH_STATUSES = (401, 403)


def _snippet(body, limit=400):
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    return (body or "")[:limit]


def classify_status(status, body=b""):
    """Raise the right error class for an HTTP status. 2xx returns None."""
    if 200 <= status < 300:
        return None
    if status in AUTH_STATUSES:
        raise AuthError("http_%d" % status, _snippet(body))
    if status in RETRYABLE_STATUSES or status >= 500:
        raise AvailabilityError("http_%d" % status, _snippet(body))
    if 400 <= status < 500:
        # The endpoint or the request contract moved under us. Never silent.
        raise ContractError("http_%d" % status, _snippet(body))
    raise AvailabilityError("http_%d" % status, _snippet(body))


class RealClock:
    def now_ms(self):
        return int(time.monotonic() * 1000)

    def sleep_ms(self, ms):
        time.sleep(ms / 1000.0)


class FakeClock:
    """Test clock. Only sleep advances it, so attempt timestamps measure the
    scheduler, not wall time. Real waits are only ever measured in the live gate.
    """

    def __init__(self):
        self._t = 0

    def now_ms(self):
        return self._t

    def sleep_ms(self, ms):
        self._t += int(ms)


class UrllibTransport:
    def __init__(self, timeout=60):
        self.timeout = timeout

    def post(self, url, headers, body):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError) as exc:
            raise AvailabilityError("transport_%s" % type(exc).__name__, str(exc))


class StubTransport:
    """Test-only. Replays a script of canned responses.

    Recorded responses always match our own expectations, so this can never
    detect real drift. That job belongs to verify_live.py, and live_check
    actively refuses anything that looks recorded.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    @classmethod
    def from_file(cls, path):
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(data["responses"])

    def post(self, url, headers, body):
        self.calls.append({"url": url, "body": body})
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        step = self.script[idx]
        if step.get("raise"):
            raise AvailabilityError("transport_%s" % step["raise"], "stubbed")
        raw = step.get("body")
        if raw is None and "body_json" in step:
            raw = json.dumps(step["body_json"], ensure_ascii=False)
        return int(step["status"]), (raw or "").encode("utf-8")


def backoff_ms(attempt):
    """attempt is 1-based. The cap is reachable at attempt == MAX_RETRIES."""
    raw = core.BACKOFF_BASE_MS * (core.BACKOFF_FACTOR ** (attempt - 1))
    return min(raw, core.BACKOFF_CAP_MS)


class Client:
    def __init__(self, transport, api_key, *, clock=None, emitter=None, endpoint=core.ENDPOINT):
        self.transport = transport
        self.api_key = api_key
        self.clock = clock or RealClock()
        self.emitter = emitter
        self.endpoint = endpoint
        self.attempts = 0
        self.attempt_times_ms = []
        self.waits_ms = []
        self.last_status = None

    def build_headers(self):
        """The only place the key is ever put on the wire."""
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % (self.api_key or ""),
        }

    def _log(self, msg):
        if self.emitter:
            self.emitter.log(msg)

    def complete(self, payload):
        """Return (status, raw_bytes). Retries availability failures only."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self.build_headers()
        last = None
        for attempt in range(1, core.MAX_RETRIES + 2):
            self.attempts = attempt
            self.attempt_times_ms.append(self.clock.now_ms())
            self._log("attempt %d -> %s" % (attempt, self.endpoint))
            try:
                status, raw = self.transport.post(self.endpoint, headers, body)
                self.last_status = status
                classify_status(status, raw)
                self._log("attempt %d ok status=%d bytes=%d" % (attempt, status, len(raw)))
                return status, raw
            except AuthError as exc:
                self._log("attempt %d auth failure %s" % (attempt, exc.reason))
                raise
            except ContractError as exc:
                self._log("attempt %d contract failure %s" % (attempt, exc.reason))
                raise
            except AvailabilityError as exc:
                last = exc
                self._log("attempt %d availability failure %s" % (attempt, exc.reason))
                if attempt > core.MAX_RETRIES:
                    break
                wait = backoff_ms(attempt)
                self.waits_ms.append(wait)
                self._log("backoff %dms" % wait)
                self.clock.sleep_ms(wait)
        raise AvailabilityError(
            "api_unreachable",
            "%d attempts, last=%s" % (self.attempts, getattr(last, "reason", "?")),
        )
