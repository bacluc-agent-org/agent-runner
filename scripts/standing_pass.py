#!/usr/bin/env python3
"""Collect standing-pass evidence and publish one validated follow-up issue."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


WORKFLOWS = ("hourly-issue.yml", "opencode.yml", "refine-issues.yml", "review-fixes.yml")
RUN_URL = "https://github.com/bacluc-agent-org/agent-runner/actions/runs/"


def run_gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def baseline_run_id(issue_repository: str) -> int:
    comments = json.loads(run_gh("issue", "view", "206", "-R", issue_repository, "--json", "comments"))["comments"]
    ids = [int(value) for comment in comments for value in re.findall(RUN_URL + r"(\d+)", comment.get("body", ""))]
    return max(ids, default=0)


def collect(issue_repository: str, runner_repository: str) -> dict[str, object]:
    baseline = baseline_run_id(issue_repository)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    runs = []
    for workflow in WORKFLOWS:
        values = json.loads(
            run_gh(
                "run",
                "list",
                "-R",
                runner_repository,
                "-w",
                workflow,
                "--limit",
                "50",
                "--json",
                "databaseId,name,conclusion,createdAt",
            )
        )
        for value in values:
            created = datetime.fromisoformat(value["createdAt"].replace("Z", "+00:00"))
            if value["databaseId"] <= baseline or created > cutoff:
                continue
            if value["conclusion"] not in {"failure", "cancelled", ""}:
                continue
            runs.append({"workflow": workflow, **value})
    return {"baseline_run_id": baseline, "runs": runs}


def create_follow_up(report: str, issue_repository: str, pass_id: str) -> str:
    title_match = re.search(r"^FOLLOW_UP_TITLE:\s*(.+)$", report, re.MULTILINE)
    body_match = re.search(r"^FOLLOW_UP_BODY_BEGIN\s*\n(.*?)^FOLLOW_UP_BODY_END\s*$", report, re.MULTILINE | re.DOTALL)
    if not title_match or not body_match:
        raise ValueError("standing pass report is missing its required output markers")
    title = title_match.group(1).strip()
    body = body_match.group(1).strip() + f"\n\n<!-- standing-pass:{pass_id} -->\n"
    if not title or not body.startswith("## Goal\n") or "\n## How to implement\n" not in body:
        raise ValueError("standing pass report does not match the refined issue schema")
    if len(set(re.findall(re.escape(RUN_URL) + r"(\d+)", body))) < 2:
        raise ValueError("standing pass report lacks two distinct action-run citations")
    existing = run_gh("api", "--paginate", f"repos/{issue_repository}/issues?state=all&per_page=100", "--jq", ".[] | .body // \"\"")
    if f"<!-- standing-pass:{pass_id} -->" in existing:
        return "already-created"
    path = Path("/tmp/standing-follow-up.md")
    path.write_text(body)
    return run_gh("issue", "create", "-R", issue_repository, "--title", title, "--body-file", str(path)).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--issue-repository", default="bacluc-agent/agent-todo")
    parser.add_argument("--runner-repository", default="bacluc-agent-org/agent-runner")
    parser.add_argument("--pass-id")
    parser.add_argument("--report")
    args = parser.parse_args()
    if args.collect:
        print(json.dumps(collect(args.issue_repository, args.runner_repository)))
    elif args.create and args.pass_id and args.report:
        print(create_follow_up(Path(args.report).read_text(), args.issue_repository, args.pass_id))
    else:
        parser.error("choose --collect or --create with --pass-id and --report")


if __name__ == "__main__":
    main()
