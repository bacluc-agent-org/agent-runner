#!/usr/bin/env python3
"""Classify the coordinator result from the real agent exit status, not tee."""

import argparse


def classify(coordinator_status: str | int, fatal_reason: str = "") -> str:
    try:
        status = int(coordinator_status)
    except (TypeError, ValueError):
        status = None

    if status == 0:
        return "✅ completed"
    if fatal_reason:
        return f"❌ fatal error: {fatal_reason}"
    if status is None:
        return "⚠️ failed (coordinator did not report an exit status)"
    if status == 124:
        return "⚠️ failed (coordinator timeout)"
    return f"⚠️ failed (coordinator exit {status})"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coordinator-status", default="")
    parser.add_argument("--fatal-reason", default="")
    args = parser.parse_args()
    print(classify(args.coordinator_status, args.fatal_reason))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
