#!/usr/bin/env python3
"""Fail-closed validation for a coordinator's claimed delivery."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class DeliveryError(ValueError):
    pass


PR_URL = re.compile(r"^https://github\.com/([^/\s]+)/([^/\s]+)/pull/[1-9][0-9]*$")
MARKER = "<!-- agent-progress -->"
HEADINGS = (
    "### Management summary",
    "### Design decisions",
    "### Open PR",
    "### Verification evidence",
    "### Action runs",
)
PLACEHOLDERS = {"task complete", "done", "completed", "implementation complete"}


def _substantive(value: Any) -> bool:
    text = " ".join(str(value or "").split())
    return len(text) >= 20 and text.casefold() not in PLACEHOLDERS


def _references(text: Any, target_issue: str, target_run: str) -> bool:
    return target_issue in str(text or "") and target_run in str(text or "")


def _valid_progress(progress: Any, target_issue: str, target_run: str) -> bool:
    if not isinstance(progress, dict):
        return False
    body = str(progress.get("body") or "")
    if body.count(MARKER) != 1 or any(body.count(heading) != 1 for heading in HEADINGS):
        return False
    summary = body.split("### Management summary", 1)[1].split("### ", 1)[0]
    return (
        _substantive(summary)
        and "| Run | Model | What happened | Result | Failure reason |" in body
        and _references(body, target_issue, target_run)
    )


def validate_delivery(delivery: dict[str, Any]) -> str:
    """Return ``code`` or ``report``; raise DeliveryError for every invalid outcome."""
    if not isinstance(delivery, dict):
        raise DeliveryError("delivery must be an object")
    coordinator = delivery.get("coordinator")
    if not isinstance(coordinator, dict) or coordinator.get("outcome") != "success":
        raise DeliveryError("coordinator did not succeed")
    if coordinator.get("fatal"):
        raise DeliveryError("coordinator reported a fatal failure")
    if not _substantive(delivery.get("summary")):
        raise DeliveryError("delivery summary is missing or only a placeholder")
    target_repo = str(delivery.get("target_repo") or "")
    target_issue = str(delivery.get("target_issue") or "")
    target_run = str(delivery.get("target_run") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target_repo):
        raise DeliveryError("target repository is missing or malformed")
    if not re.fullmatch(r"https://github\.com/[^/\s]+/[^/\s]+/issues/[1-9][0-9]*", target_issue):
        raise DeliveryError("target issue reference is missing or malformed")
    if not re.fullmatch(rf"https://github\.com/{re.escape(target_repo)}/actions/runs/[1-9][0-9]*", target_run):
        raise DeliveryError("target run reference is missing or malformed")
    if not _references(delivery.get("summary"), target_issue, target_run):
        raise DeliveryError("delivery summary lacks target issue and run references")
    if delivery.get("push_outcome") != "success":
        raise DeliveryError("work push did not succeed")

    changed = delivery.get("changed")
    prs = delivery.get("prs")
    if not isinstance(prs, list):
        raise DeliveryError("pull requests must be a list")
    if changed is True:
        if not delivery.get("pushed") or not delivery.get("branch"):
            raise DeliveryError("code changed without a pushed branch")
        if len(prs) != 1:
            raise DeliveryError("code delivery must have exactly one pull request")
        pr = prs[0]
        if not isinstance(pr, dict) or pr.get("state", "").upper() != "OPEN":
            raise DeliveryError("pull request is not open")
        match = PR_URL.fullmatch(str(pr.get("url") or ""))
        if not match or f"{match.group(1)}/{match.group(2)}" != target_repo:
            raise DeliveryError("pull request URL is malformed")
        if not _substantive(pr.get("body")) or not _references(pr.get("body"), target_issue, target_run):
            raise DeliveryError("pull request body lacks target issue and run references")
        return "code"

    if changed is not False:
        raise DeliveryError("delivery must state whether code changed")
    if prs or delivery.get("pushed") or delivery.get("branch"):
        raise DeliveryError("no-code delivery must not claim a branch or pull request")
    if not _valid_progress(delivery.get("progress"), target_issue, target_run):
        raise DeliveryError("no-code delivery lacks a canonical progress report")
    return "report"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_delivery(json.loads(args.delivery.read_text(encoding="utf-8")))
    except (DeliveryError, OSError, json.JSONDecodeError) as error:
        print(f"invalid delivery: {error}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
