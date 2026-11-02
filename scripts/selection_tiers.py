#!/usr/bin/env python3
"""Rank issue candidates mechanically, then randomize their presentation."""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Candidate:
    number: int
    title: str
    labels: list[str]
    created_at: str
    pr_state: str
    pr_number: int | None
    pr_last_commit_at: str | None
    last_human_comment_at: str | None
    attempts: int
    recent_attempts: int
    body: str = ""


def parse_branches(names: list[str]) -> list[tuple[int, int]]:
    parsed = []
    for name in names:
        match = re.fullmatch(r"(?:agent-run/|issue-)(\d+)-(\d+)", name.strip())
        if match:
            parsed.append((int(match.group(1)), int(match.group(2))))
    return sorted(parsed, key=lambda item: item[1], reverse=True)


def render_prompt(candidates: list[Candidate], tail_text: str) -> str:
    selected = list(candidates)
    random.shuffle(selected)
    lines = [
        "All candidates (RANDOM ORDER — position carries no priority):"
    ]
    for candidate in selected:
        body = candidate.body.replace("\r", " ").replace("\n", " ").replace("|", "¦")[:300]
        title = candidate.title.replace("\r", " ").replace("\n", " ")
        lines.append(
            f"{candidate.number}: {title} [labels: {','.join(candidate.labels)}] "
            f"[created: {candidate.created_at}] [PR: {candidate.pr_state} "
            f"#{candidate.pr_number or 'none'} last-commit:{candidate.pr_last_commit_at or 'none'}] "
            f"[last-human-feedback:{candidate.last_human_comment_at or 'none'}] | {body} "
            f"[attempts: {candidate.attempts}] [recent-attempts: {candidate.recent_attempts}]"
        )
    lines.append(tail_text.strip())
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--branches", required=True)
    parser.add_argument("--tail-file", required=True)
    args = parser.parse_args()
    issues = json.loads(Path(args.candidates).read_text())
    runs = parse_branches(Path(args.branches).read_text().splitlines())
    newest_three = [number for number, _ in runs[:3]]
    candidates = []
    for issue in issues:
        number = int(issue["number"])
        issue_runs = [run for candidate, run in runs if candidate == number]
        labels = [label["name"] for label in issue.get("labels", [])]
        attempts = max(len(issue_runs), int(any(label.casefold() == "agent-attempted" for label in labels)))
        candidate = Candidate(
            number=number,
            title=issue.get("title", ""),
            labels=labels,
            created_at=issue.get("created_at", ""),
            pr_state=issue.get("pr_state", "none").lower(),
            pr_number=issue.get("pr_number"),
            pr_last_commit_at=issue.get("pr_last_commit_at"),
            last_human_comment_at=issue.get("last_human_comment_at"),
            attempts=attempts,
            recent_attempts=sum(number == recent for recent in newest_three),
            body=issue.get("body") or "",
        )
        candidates.append(candidate)
    for candidate in candidates:
        print(f"attempts for {candidate.number}: {candidate.attempts}", file=sys.stderr)
    print(render_prompt(candidates, Path(args.tail_file).read_text()), end="")


if __name__ == "__main__":
    main()
