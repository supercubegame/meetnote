"""Single choke point for anything that leaves the process.

Every byte written to stdout, stderr, the log file or the diagnostic snapshot
passes through Redactor. If you add a new write path, route it through here, or
the secret-hygiene checks will catch you: they scan all sinks for a sentinel.
"""
from __future__ import annotations

import json
import sys

MASK = "***REDACTED***"


class Redactor:
    def __init__(self, secrets=()):
        keep = []
        for s in secrets:
            # Short values are ignored on purpose: masking a 3-char string would
            # shred normal output. Real keys are long.
            if isinstance(s, str) and len(s) >= 8 and s not in keep:
                keep.append(s)
        keep.sort(key=len, reverse=True)
        self._secrets = keep

    def secret_count(self):
        return len(self._secrets)

    def scrub(self, text):
        if not isinstance(text, str):
            text = str(text)
        for s in self._secrets:
            if s in text:
                text = text.replace(s, MASK)
        return text


class Emitter:
    def __init__(self, redactor, log_path=None, debug=False):
        self.redactor = redactor
        self.log_path = log_path
        self.debug = debug
        self._log_lines = []

    def log(self, message):
        line = self.redactor.scrub(message)
        self._log_lines.append(line)
        if self.debug:
            sys.stderr.write("[debug] " + line + "\n")

    def err(self, message):
        sys.stderr.write(self.redactor.scrub(message) + "\n")

    def out_json(self, obj):
        payload = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
        sys.stdout.write(self.redactor.scrub(payload) + "\n")

    def dump_json(self, path, obj):
        payload = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.redactor.scrub(payload) + "\n")

    def flush_log(self):
        if not self.log_path:
            return
        with open(self.log_path, "a", encoding="utf-8") as fh:
            for line in self._log_lines:
                fh.write(self.redactor.scrub(line) + "\n")
