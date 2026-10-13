#!/usr/bin/env python3
"""Rewrite current-repo references in issues from bacluc-agent/agent-runner to bacluc-agent-org/agent-runner.

The repository moved to the organization fork, but historical artifact links
(`/actions/runs/...`, `/pull/...`, `/issues/...`, `#NNN`, `--repo`, `repos/`)
still point at the old owner and must stay byte-for-byte intact because only the
old repository holds those artifacts.

Replacement rule (pure function `rewrite_text`): for every match of
`bacluc-agent/agent-runner` not followed by `/`, `-`, `#`, or a word character,
replace it unless
  - the text immediately before the match ends with `--repo `, `-R `,
    `--repository `, `repository: `, or `repos/` (gh / REST args), or
  - the enclosing line contains `old repo` or `404 in the org fork`, or
  - the ~20 characters before or after the match hold a standalone `PR`, `PRs`,
    `pull`, `run`, or `runs` token (past-PR / run evidence context).
A bare repo-root URL (`https://github.com/bacluc-agent/agent-runner`) is an
identity reference and is rewritten. The rewrite is idempotent.

Default is a dry run; `--apply` edits only the issues whose title or body changed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

OLD = "bacluc-agent/agent-runner"
NEW = "bacluc-agent-org/agent-runner"
PATTERN = re.compile(re.escape(OLD) + r"(?![/\-#\w])")
PRESERVE_PREFIXES = ("--repo ", "-R ", "--repository ", "repository: ", "repos/")
PRESERVE_MARKERS = ("old repo", "404 in the org fork")
TOKEN = re.compile(r"\b(?:PRs?|pull|runs?)\b")
TOKEN_WINDOW = 20


def run_gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _preserve(text: str, start: int, end: int) -> bool:
    if re.match(r"[/\-#\w]", text[end : end + 1]):
        return True
    before = text[:start]
    if before.endswith(PRESERVE_PREFIXES):
        return True
    line_start = before.rfind("\n") + 1
    line_end = text.find("\n", end)
    line = text[line_start : len(text) if line_end == -1 else line_end]
    if any(marker in line for marker in PRESERVE_MARKERS):
        return True
    return TOKEN.search(text[max(0, start - TOKEN_WINDOW) : end + TOKEN_WINDOW]) is not None


def rewrite_text(text: str) -> tuple[str, int]:
    """Return the rewritten text and how many identity references were replaced."""
    parts: list[str] = []
    last = 0
    count = 0
    for match in PATTERN.finditer(text):
        if _preserve(text, match.start(), match.end()):
            continue
        parts.append(text[last : match.start()])
        parts.append(NEW)
        last = match.end()
        count += 1
    if not count:
        return text, 0
    parts.append(text[last:])
    return "".join(parts), count


def list_issues(repo: str, issue: int | None) -> list[dict]:
    if issue is not None:
        raw = run_gh("issue", "view", str(issue), "-R", repo, "--json", "number,title,body")
        return [json.loads(raw)]
    raw = run_gh(
        "issue",
        "list",
        "-R",
        repo,
        "--state",
        "all",
        "--limit",
        "1000",
        "--json",
        "number,title,body",
    )
    return json.loads(raw)


def edit_issue(repo: str, number: int, title: str, body: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", encoding="utf-8", newline="", delete=False
    ) as handle:
        handle.write(body)
        path = handle.name
    try:
        run_gh("issue", "edit", str(number), "-R", repo, "--title", title, "--body-file", path)
    finally:
        os.unlink(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="bacluc-agent/agent-todo")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--issue", type=int)
    args = parser.parse_args(argv)

    try:
        issues = list_issues(args.repo, args.issue)
    except (subprocess.CalledProcessError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    changed = 0
    for issue in issues:
        title, title_count = rewrite_text(issue.get("title") or "")
        body, body_count = rewrite_text(issue.get("body") or "")
        if not title_count + body_count:
            continue
        changed += 1
        print(f"#{issue['number']} ({title_count + body_count})")
        if args.apply:
            edit_issue(args.repo, issue["number"], title, body)

    print(f"SCANNED={len(issues)} CHANGED={changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
