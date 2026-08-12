#!/usr/bin/env python3
"""Slow gate entry point. Talks to the real StepFun endpoint exactly once.

Exit codes:
  0  live response matched the contract
  1  drift, or a replay guard failed
 78  unconfirmed: no secret, API unavailable, or credential rejected

Never prints the key: the whole report is scrubbed before it is written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from meetnote import live_check
from meetnote.output import Redactor


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verify_live")
    parser.add_argument("--report", default="reports/live.json")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    api_key = os.environ.get("STEPFUN_API_KEY", "")
    redactor = Redactor([api_key])
    report = live_check.run_live(api_key=api_key, root=args.root)
    payload = redactor.scrub(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if args.report:
        directory = os.path.dirname(args.report)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")

    sys.stdout.write(payload + "\n")
    print(
        "live gate: status=%s checks=%d/%d drift=%d unconfirmed=%d"
        % (
            report["status"],
            report["checks_passed"],
            report["checks_run"],
            len(report["drift"]),
            len(report["unconfirmed_reasons"]),
        )
    )
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
