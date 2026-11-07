#!/usr/bin/env python3
import os


def classify(coordinator_status, tee_status, outcome, fatal):
    try:
        coordinator = int(coordinator_status)
    except (TypeError, ValueError):
        coordinator = None

    if coordinator == 0:
        try:
            tee = int(tee_status)
        except (TypeError, ValueError):
            tee = 0
        if tee:
            return f"✅ completed (log capture failed, tee exit {tee})"
        return "✅ completed"
    if fatal:
        return f"❌ fatal error: {fatal}"
    if outcome in ("cancelled", "skipped"):
        return "⚠️ cancelled before the coordinator finished"
    if coordinator == 124:
        return "⏱️ timed out (coordinator exit 124)"
    if coordinator is not None:
        return f"⚠️ failed (coordinator exit {coordinator})"
    return f"⚠️ failed (coordinator exit {outcome})"


def main():
    print(
        classify(
            os.environ.get("COORDINATOR_STATUS", ""),
            os.environ.get("COORDINATOR_TEE_STATUS", ""),
            os.environ.get("COORDINATOR_OUTCOME", ""),
            os.environ.get("FATAL_REASON", ""),
        )
    )
    return 0


if __name__ == "__main__":
    main()
