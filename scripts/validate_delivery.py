#!/usr/bin/env python3
"""Validate the coordinator's delivery against its existing progress report."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from update_progress_comment import find_comment


def validate_delivery(output, issue_url, run_url, comment, pushed, fetch):
    records = re.findall(r"(?m)^AGENT_OUTCOME: (.+)$", output)
    if len(records) != 1:
        raise ValueError("expected exactly one outcome record")
    try:
        record = json.loads(records[0])
    except json.JSONDecodeError as error:
        raise ValueError("invalid outcome record") from error
    if not isinstance(record, dict) or record.get("status") != "completed":
        raise ValueError("outcome is not completed")
    summary = record.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < 40 or len(summary.split()) < 7:
        raise ValueError("outcome summary is not substantive")
    if re.fullmatch(r"(?i)(?:\s*(?:placeholder|todo|tbd|lorem|ipsum)[\s.,;:!?-]*)+", summary):
        raise ValueError("outcome summary is a placeholder")
    kind = record.get("kind")
    if kind == "report":
        if pushed:
            raise ValueError("a recovery branch is not a delivered report")
        if issue_url:
            match = re.search(r"(?ms)^### Management summary\s*\n(.*?)(?=^### |\Z)", comment)
            if not match or summary not in match.group(1) or run_url not in match.group(1):
                raise ValueError("current report missing from canonical progress comment")
        return ""
    if kind != "code":
        raise ValueError("outcome must identify code or report delivery")
    if pushed:
        raise ValueError("a recovery branch is not a delivered pull request")
    url = record.get("url")
    if not isinstance(url, str):
        raise ValueError("missing pull request URL")
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)", url)
    if not match:
        raise ValueError("invalid pull request URL")
    pr = fetch(f"repos/{match.group(1)}/pulls/{match.group(2)}")
    if pr.get("state") != "open" or not isinstance(pr.get("changed_files"), int) or pr["changed_files"] < 1:
        raise ValueError("pull request is not open or has no changes")
    body = pr.get("body") or ""
    for required_url in (issue_url, run_url):
        if required_url and not re.search(re.escape(required_url) + r"(?![A-Za-z0-9_/-])", body):
            raise ValueError("pull request does not reference this task and run")
    return url


def main():
    issue = os.environ.get("ISSUE_NUMBER", "")
    repo = os.environ["ISSUE_REPOSITORY"]
    run_url = f'{os.environ["GITHUB_SERVER_URL"]}/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    issue_url = f"https://github.com/{repo}/issues/{issue}" if issue else ""
    try:
        comment = find_comment(issue, repo) if issue else None
        def fetch(path):
            return json.loads(subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True).stdout)
        url = validate_delivery(Path(os.environ["RUNNER_TEMP"], "coordinator.out").read_text(), issue_url, run_url, (comment or {}).get("body", ""), bool(os.environ.get("PUSHED_REPOS")), fetch)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"::error::Incomplete delivery: {error}", file=sys.stderr)
        return 1
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        print(f"pr_url={url}", file=output)
    print("Delivery validated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
