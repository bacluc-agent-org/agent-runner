#!/usr/bin/env python3
"""Keep the single agent progress comment on an issue up to date — stdlib only, no new dependencies."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = "<!-- agent-progress -->"
TEMPLATE = Path(__file__).with_name("progress-comment-template.md")
BODY_LIMIT = 65536


def run_gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def _cell(value: str) -> str:
    """One table cell: no line breaks, no pipe that would split the row."""
    return " ".join(value.split()).replace("|", "\\|")


def _row(run_url: str, model: str, summary: str, result: str, failure_reason: str) -> str:
    """One run row. An empty result is the pending row of a dispatch that is still running."""
    cells = [
        _cell(run_url),
        _cell(model),
        _cell(summary or "run started"),
        _cell(result or "pending"),
        _cell(failure_reason),
    ]
    return "| " + " | ".join(cells) + " |"


def _is_data_row(line: str) -> bool:
    return line.startswith("| ") and not line.startswith("| Run") and not set(line) <= set("| -")


def upsert_row(lines: list[str], row: str) -> None:
    """Insert or replace the row of this run URL, keeping the other rows in order.

    Data rows are the '| ' lines right below the table header; that range ends at the
    first line that is not a table row, so the <details> block is never touched.
    """
    header = next((i for i, line in enumerate(lines) if line.startswith("| Run")), None)
    if header is None:
        raise ValueError(f"no '### Action runs' table header in the comment body ({MARKER})")
    start, end = header + 2, header + 2
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    run_url = _cell(row.removeprefix("| ").split(" | ")[0])
    for index in range(start, end):
        if _cell(lines[index].removeprefix("| ").split(" | ")[0]) == run_url:
            lines[index] = row
            return
    lines.insert(end, row)


def set_open_pr(lines: list[str], pr_urls: list[str]) -> None:
    """Replace the whole '### Open PR' section with one bare pull request URL per line."""
    if not pr_urls:
        return
    start = lines.index("### Open PR")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("### ")),
        len(lines),
    )
    lines[start + 1 : end] = ["", *pr_urls, ""]


def authenticated_login() -> str:
    return run_gh("api", "user", "--jq", ".login").strip()


def find_comment(issue: str, repo: str) -> dict | None:
    """The newest marker comment of the authenticated user, or None.

    gh merges the paginated pages into one array; older versions print one array per
    page, so flatten that shape too. The newest marker comment is the canonical one.
    """
    comments = json.loads(
        run_gh("api", "--paginate", f"repos/{repo}/issues/{issue}/comments")
    )
    if comments and isinstance(comments[0], list):
        comments = [comment for page in comments for comment in page]
    return max(
        (
            comment
            for comment in comments
            if MARKER in (comment.get("body") or "")
        ),
        key=lambda comment: comment["created_at"],
        default=None,
    )


def send_comment(repo: str, issue: str, body: str, comment_id: str | None) -> dict:
    """Create (POST) or replace (PATCH) the comment body from a temp file.

    -F, not -f: -f is --raw-field and would send the literal '@<file>' string, which
    replaces the comment body with that text.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", encoding="utf-8", newline="", delete=False
    ) as handle:
        handle.write(body)
        path = handle.name
    try:
        size = len(body.encode())
        if size > BODY_LIMIT:
            print(
                f"warning: comment body is {size} bytes, over the {BODY_LIMIT} byte limit",
                file=sys.stderr,
            )
        if comment_id:
            return json.loads(
                run_gh(
                    "api",
                    "-X",
                    "PATCH",
                    f"repos/{repo}/issues/comments/{comment_id}",
                    "-F",
                    f"body=@{path}",
                )
            )
        return json.loads(
            run_gh(
                "api", "-X", "POST", f"repos/{repo}/issues/{issue}/comments", "-F", f"body=@{path}"
            )
        )
    finally:
        os.unlink(path)


def write_outputs(output_file: str, comment: dict) -> None:
    """Print the comment outputs and append them to the step output file."""
    outputs = f"comment_id={comment['id']}\ncomment_url={comment['html_url']}\n"
    sys.stdout.write(outputs)
    if output_file:
        with open(output_file, "a", encoding="utf-8") as handle:
            handle.write(outputs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", required=True)
    parser.add_argument("--repo", default=os.environ.get("ISSUE_REPOSITORY", ""))
    parser.add_argument("--model", default="")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--run-summary", default="")
    parser.add_argument("--result", default="")
    parser.add_argument("--failure-reason", default="")
    parser.add_argument("--pr-url", default="")
    parser.add_argument("--mode", choices=("ensure", "finish"), required=True)
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args(argv)

    if not args.repo:
        print("error: --repo is required (or set ISSUE_REPOSITORY)", file=sys.stderr)
        return 1
    try:
        template = TEMPLATE.read_text(encoding="utf-8").split("\n")
    except OSError as error:
        print(f"error: cannot read the comment template {TEMPLATE}: {error}", file=sys.stderr)
        return 1

    try:
        comment = find_comment(args.issue, args.repo)
        if args.mode == "ensure" and comment is not None:
            write_outputs(args.github_output, comment)
            return 0
        if comment is None:
            lines = template
            if args.mode == "finish":
                print(
                    "warning: no progress comment found; creating it from the template",
                    file=sys.stderr,
                )
        else:
            lines = (comment.get("body") or "").split("\n")
        upsert_row(
            lines, _row(args.run_url, args.model, args.run_summary, args.result, args.failure_reason)
        )
        if args.mode == "finish":
            set_open_pr(lines, args.pr_url.split())
        comment = send_comment(
            args.repo, args.issue, "\n".join(lines), str(comment["id"]) if comment else None
        )
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    write_outputs(args.github_output, comment)
    return 0


if __name__ == "__main__":
    sys.exit(main())
