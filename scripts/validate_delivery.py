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
REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
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
    lines = body.splitlines()
    if not lines or lines[0] != MARKER or lines.count(MARKER) != 1:
        return False
    positions = [[index for index, line in enumerate(lines) if line == heading] for heading in HEADINGS]
    if any(len(found) != 1 for found in positions):
        return False
    heading_positions = [found[0] for found in positions]
    if heading_positions != sorted(heading_positions):
        return False
    if any(line.startswith("### ") and line not in HEADINGS for line in lines):
        return False
    sections = {
        heading: lines[start + 1 : end]
        for heading, start, end in zip(HEADINGS, heading_positions, heading_positions[1:] + [len(lines)])
    }
    if not _substantive("\n".join(sections[HEADINGS[0]])):
        return False
    if not "\n".join(sections[HEADINGS[1]]).strip() or not _references(body, target_issue, target_run):
        return False
    table = sections[HEADINGS[-1]]
    try:
        header = next(index for index, line in enumerate(table) if line == "| Run | Model | What happened | Result | Failure reason |")
    except StopIteration:
        return False
    if header + 1 >= len(table) or not (
        table[header + 1].startswith("|")
        and table[header + 1].endswith("|")
        and len(table[header + 1][1:-1].split("|")) == 5
        and all(re.fullmatch(r"\s*-+\s*", cell) for cell in table[header + 1][1:-1].split("|"))
    ):
        return False
    rows = []
    for line in table[header + 2 :]:
        if not line:
            break
        if not (line.startswith("|") and line.endswith("|")):
            return False
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 5 or not all(cells[:4]):
            return False
        rows.append(cells)
    return any(row[0] == target_run and row[2] and row[3] for row in rows)


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
    if not REPO_SLUG.fullmatch(target_repo):
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
        pushed_slugs = delivery.get("pushed_slugs")
        if not isinstance(pushed_slugs, list) or not pushed_slugs:
            raise DeliveryError("code delivery lacks pushed repository slugs")
        if any(not isinstance(slug, str) or not REPO_SLUG.fullmatch(slug) for slug in pushed_slugs):
            raise DeliveryError("pushed repository slugs are malformed")
        expected = set(pushed_slugs)
        if len(expected) != len(pushed_slugs) or len(prs) != len(expected):
            raise DeliveryError("code delivery must have exactly one pull request per pushed repository")
        delivered = set()
        for pr in prs:
            if not isinstance(pr, dict) or pr.get("state", "").upper() != "OPEN":
                raise DeliveryError("pull request is not open")
            match = PR_URL.fullmatch(str(pr.get("url") or ""))
            slug = f"{match.group(1)}/{match.group(2)}" if match else ""
            if slug not in expected or slug in delivered:
                raise DeliveryError("pull request repository does not match pushed repositories")
            if not _substantive(pr.get("body")) or not _references(pr.get("body"), target_issue, target_run):
                raise DeliveryError("pull request body lacks target issue and run references")
            delivered.add(slug)
        if delivered != expected:
            raise DeliveryError("code delivery is missing a pull request for a pushed repository")
        return "code"

    if changed is not False:
        raise DeliveryError("delivery must state whether code changed")
    if prs or delivery.get("pushed") or delivery.get("branch") or delivery.get("pushed_slugs"):
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
