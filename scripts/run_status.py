#!/usr/bin/env python3
"""Run-outcome classifier — stdlib only, no new dependencies."""
import os
import sys


def classify(
    coordinator_status: str,
    coordinator_tee_status: str,
    coordinator_outcome: str,
    select_model_outcome: str,
    fatal_reason: str,
    branch_name: str,
    pushed_repos: str,
    timeout_minutes: int,
) -> tuple[str, int]:
    # ponytail: any non-main branch counts as delivered, including a scratch branch; tighten if a run passes the gate having committed nothing.
    delivered = bool(branch_name.strip()) or bool(pushed_repos.strip())
    if select_model_outcome != "success":
        return f"❌ failed (model selection {select_model_outcome}; coordinator never started)", 1
    if fatal_reason:
        return f"❌ fatal error: {fatal_reason}", 1
    if coordinator_outcome == "cancelled":
        return "⚠️ cancelled (coordinator job cancelled)", 1
    if not coordinator_status.isdigit():
        return f"⚠️ failed (coordinator step aborted before reporting an exit code; outcome={coordinator_outcome})", 1
    if coordinator_status == "0" and coordinator_tee_status in ("", "0"):
        return "✅ completed", 0
    if coordinator_status == "124":
        return f"⚠️ timed out after {timeout_minutes}m (coordinator exit 124)", 0 if delivered else 1
    return f"⚠️ failed (coordinator exit {coordinator_status})", 0 if delivered else 1


def main() -> int:
    timeout_raw = os.environ.get("COORDINATOR_TIMEOUT", "")
    try:
        timeout_minutes = int(timeout_raw)
    except ValueError:
        timeout_minutes = 120
    status, code = classify(
        os.environ.get("COORDINATOR_STATUS", ""),
        os.environ.get("COORDINATOR_TEE_STATUS", ""),
        os.environ.get("COORDINATOR_OUTCOME", ""),
        os.environ.get("SELECT_MODEL_OUTCOME", ""),
        os.environ.get("FATAL_REASON", ""),
        os.environ.get("BRANCH_NAME", ""),
        os.environ.get("PUSHED_REPOS", ""),
        timeout_minutes,
    )
    print(status)
    if "--enforce" not in sys.argv:
        return 0
    print(status, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
