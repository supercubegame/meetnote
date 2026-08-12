#!/usr/bin/env python3
"""Fast gate. Zero network, zero third-party dependencies.

Modules are enumerated explicitly instead of discovered: directory conventions
and shell globs are exactly how a gate ends up reporting "all green" without
having executed anything. The check count is asserted by equality, and a module
that contributes zero checks is a hard error rather than a quiet skip.

Exit 0 pass, exit 1 fail. The report is written to disk for CI to pick up.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
import unittest

from tests.expected_counts import EXPECTED_CHECKS

MODULES = (
    "tests.test_core",
    "tests.test_client",
    "tests.test_cli_e2e",
    "tests.test_meta",
)


def iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for inner in iter_tests(item):
                yield inner
        else:
            yield item


def tail(text, lines=14):
    kept = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def ensure_parent(path):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify")
    parser.add_argument("--report", default="reports/fast.json")
    parser.add_argument("--log", default="reports/fast.log")
    args = parser.parse_args(argv)

    loader = unittest.TestLoader()
    stream = io.StringIO()
    per_module = {}
    failures = []
    hard_errors = []
    total = 0
    started = time.time()

    for name in MODULES:
        try:
            suite = loader.loadTestsFromName(name)
        except Exception as exc:  # noqa: BLE001 - a load failure must be loud
            hard_errors.append("module_did_not_load:%s (%s: %s)" % (name, type(exc).__name__, exc))
            per_module[name] = {"run": 0, "failures": 0, "errors": 1}
            continue
        broken = [
            test.id()
            for test in iter_tests(suite)
            if "_FailedTest" in test.id() or "ModuleImportFailure" in test.id()
        ]
        if broken:
            hard_errors.append("module_did_not_run:%s (%s)" % (name, ", ".join(broken)))
        stream.write("\n===== %s =====\n" % name)
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        per_module[name] = {
            "run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
        }
        total += result.testsRun
        if result.testsRun == 0:
            hard_errors.append("module_ran_zero_checks:%s" % name)
        for test, trace in list(result.failures) + list(result.errors):
            failures.append({"test": test.id(), "summary": tail(trace)})

    count_matches = total == EXPECTED_CHECKS
    if not count_matches:
        hard_errors.append("check_count_drift: ran %d, expected %d" % (total, EXPECTED_CHECKS))

    ok = not failures and not hard_errors
    log_text = stream.getvalue()
    report = {
        "gate": "fast",
        "status": "ok" if ok else "red",
        "checks_run": total,
        "checks_passed": total - len(failures),
        "expected_checks": EXPECTED_CHECKS,
        "count_matches": count_matches,
        "duration_s": round(time.time() - started, 1),
        "per_module": per_module,
        "hard_errors": hard_errors,
        "failures": failures,
        "log_tail": tail(log_text, 25),
    }

    if args.log:
        ensure_parent(args.log)
        with open(args.log, "w", encoding="utf-8") as fh:
            fh.write(log_text)
    if args.report:
        ensure_parent(args.report)
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    print(
        "fast gate: status=%s checks=%d/%d (expected %d) failures=%d hard_errors=%d in %ss"
        % (
            report["status"],
            report["checks_passed"],
            report["checks_run"],
            EXPECTED_CHECKS,
            len(failures),
            len(hard_errors),
            report["duration_s"],
        )
    )
    for item in hard_errors:
        print("  hard: %s" % item)
    for item in failures:
        print("  fail: %s" % item["test"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
