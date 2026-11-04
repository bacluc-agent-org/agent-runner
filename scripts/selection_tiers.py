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
            f"[created: {candidate.created_at}] | {body} "
            f"[attempts: {candidate.attempts}] [recent-attempts: {candidate.recent_attempts}]"
        )
    lines.append(tail_text.strip())
    return "\n".join(lines) + "\n"



def validate_selection(text: str, numbers: set[int]) -> tuple[str, int]:
    matches = list(re.finditer(r"\bSELECTED_ISSUE:\s*(\d+)\b", text))
    if len(matches) != 1 or len(re.findall(r"\bSELECTED_ISSUE:", text)) != 1:
        raise ValueError("expected exactly one issue marker")
    match = matches[0]
    number = int(match.group(1))
    prompt = (text[:match.start()] + text[match.end():]).strip()
    if number not in numbers or not prompt:
        raise ValueError("selection must contain a prompt and an eligible issue")
    return f"{prompt}\nSELECTED_ISSUE: {number}\n", number


def implementation_prompt(repository: str, number: int) -> str:
    url = f"https://github.com/{repository}/issues/{number}"
    return f"Implement {url}. Read the full issue body and related pull requests before working. Follow repository instructions and report the outcome.\nSELECTED_ISSUE: {number}\n"


def fallback_candidate(candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None
    least_recent = min(candidate.recent_attempts for candidate in candidates)
    return random.choice([candidate for candidate in candidates if candidate.recent_attempts == least_recent])

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--branches", required=True)
    parser.add_argument("--tail-file", required=True)
    parser.add_argument("--validate")
    parser.add_argument("--fallback-repository")
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
            attempts=attempts,
            recent_attempts=sum(number == recent for recent in newest_three),
            body=issue.get("body") or "",
        )
        candidates.append(candidate)
    if args.validate:
        try:
            normalized, number = validate_selection(Path(args.validate).read_text(), {candidate.number for candidate in candidates})
            if args.fallback_repository:
                normalized = implementation_prompt(args.fallback_repository, number)
        except ValueError as error:
            print(error, file=sys.stderr)
            sys.exit(1)
        Path(args.validate).write_text(normalized)
        return
    if args.fallback_repository:
        candidate = fallback_candidate(candidates)
        if candidate is None:
            sys.exit(1)
        print(implementation_prompt(args.fallback_repository, candidate.number), end="")
        return
    for candidate in candidates:
        print(f"attempts for {candidate.number}: {candidate.attempts}", file=sys.stderr)
    print(render_prompt(candidates, Path(args.tail_file).read_text()), end="")


if __name__ == "__main__":
    main()
