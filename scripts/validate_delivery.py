#!/usr/bin/env python3
"""Fail-closed validation for a coordinator's claimed delivery."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

MARKER = "<!-- agent-progress -->"
HEADINGS = (
    "### Management summary",
    "### Design decisions",
    "### Open PR",
    "### Verification evidence",
    "### Action runs",
)
PR_URL = re.compile(r"^https://github\.com/([^/\s]+)/([^/\s]+)/pull/[1-9][0-9]*$")


class DeliveryError(ValueError):
    pass


def _has_text(value: Any) -> bool:
    return len(" ".join(str(value or "").split())) >= 20


def _valid_progress(body: Any, target_run: str) -> bool:
    lines = str(body or "").splitlines()
    if not lines or lines[0] != MARKER or lines.count(MARKER) != 1:
        return False
    positions = [
        [index for index, line in enumerate(lines) if line == heading] for heading in HEADINGS
    ]
    if any(len(found) != 1 for found in positions):
        return False
    ordered = [found[0] for found in positions]
    if ordered != sorted(ordered):
        return False
    if any(line.startswith("### ") and line not in HEADINGS for line in lines):
        return False
    table = lines[ordered[-1] + 1 :]
    try:
        header = table.index("| Run | Model | What happened | Result | Failure reason |")
    except ValueError:
        return False
    if header + 1 >= len(table) or not re.fullmatch(r"\|\s*-+(\s*\|\s*-+){4}\s*\|", table[header + 1]):
        return False
    for line in table[header + 2 :]:
        if not line:
            break
        if not (line.startswith("|") and line.endswith("|")):
            return False
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 5 or not all(cells[:4]):
            return False
        if cells[0] == target_run:
            return True
    return False


def validate_delivery(delivery: dict[str, Any]) -> str:
    coordinator = delivery.get("coordinator")
    if not isinstance(coordinator, dict) or coordinator.get("outcome") != "success":
        raise DeliveryError("coordinator did not succeed")
    if coordinator.get("fatal"):
        raise DeliveryError("coordinator reported a fatal failure")
    target_run = str(delivery.get("target_run") or "")
    target_issue = str(delivery.get("target_issue") or "")
    if not re.fullmatch(r"https://github\.com/[^/\s]+/[^/\s]+/actions/runs/[1-9][0-9]*", target_run):
        raise DeliveryError("target run reference is malformed")
    if not re.fullmatch(r"https://github\.com/[^/\s]+/[^/\s]+/issues/[1-9][0-9]*", target_issue):
        raise DeliveryError("target issue reference is malformed")
    if not _has_text(delivery.get("summary")):
        raise DeliveryError("delivery summary is missing")
    if target_issue not in str(delivery["summary"]) or target_run not in str(delivery["summary"]):
        raise DeliveryError("delivery summary lacks target references")
    if delivery.get("push_outcome") != "success":
        raise DeliveryError("work push did not succeed")
    if not _valid_progress((delivery.get("progress") or {}).get("body"), target_run):
        raise DeliveryError("canonical progress report is missing or stale")

    prs = delivery.get("prs")
    if delivery.get("changed") is not True:
        if delivery.get("changed") is not False or prs or delivery.get("branch"):
            raise DeliveryError("no-code delivery is incomplete")
        return "report"
    slugs = delivery.get("pushed_slugs")
    if not delivery.get("pushed") or not delivery.get("branch") or not isinstance(slugs, list) or not slugs:
        raise DeliveryError("code delivery lacks a pushed branch")
    if not isinstance(prs, list) or len(prs) != len(set(slugs)):
        raise DeliveryError("code delivery must have one pull request per repository")
    delivered = set()
    for pr in prs:
        if not isinstance(pr, dict) or str(pr.get("state", "")).upper() != "OPEN":
            raise DeliveryError("pull request is not open")
        match = PR_URL.fullmatch(str(pr.get("url") or ""))
        slug = f"{match.group(1)}/{match.group(2)}" if match else ""
        if slug not in slugs or slug in delivered or not _has_text(pr.get("body")):
            raise DeliveryError("pull request is missing or malformed")
        if target_issue not in pr["body"] or target_run not in pr["body"]:
            raise DeliveryError("pull request lacks target references")
        delivered.add(slug)
    if delivered != set(slugs):
        raise DeliveryError("code delivery is missing a pull request")
    return "code"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
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
