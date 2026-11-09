#!/usr/bin/env python3
"""Classify the coordinator result without considering the tee exit status."""

import argparse


def classify(coordinator_status: int, tee_status: int = 0, fatal_reason: str = "") -> tuple[str, str]:
    if fatal_reason:
        return f"❌ fatal error: {fatal_reason}", "error"
    if coordinator_status == 0:
        return "✅ completed", "success"
    if coordinator_status == 124:
        return "⚠️ failed (coordinator timeout)", "timeout"
    return f"⚠️ failed (coordinator exit {coordinator_status})", "error"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coordinator-status", type=int, required=True)
    parser.add_argument("--tee-status", type=int, default=0)
    parser.add_argument("--fatal-reason", default="")
    parser.add_argument("--field", choices=("status", "failure-mode"), default="status")
    args = parser.parse_args()
    status, failure_mode = classify(args.coordinator_status, args.tee_status, args.fatal_reason)
    print(status if args.field == "status" else failure_mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
