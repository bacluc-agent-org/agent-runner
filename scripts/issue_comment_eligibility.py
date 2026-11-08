#!/usr/bin/env python3
"""Select comments that represent new human feedback."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

MARKER = "<!-- agent-progress -->"
DEFAULT_AGENT_LOGIN = "bacluc-agent"


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_qualifying_comment(comment: dict, agent_login: str = DEFAULT_AGENT_LOGIN) -> bool:
    user = comment.get("user") or {}
    return (
        user.get("type") != "Bot"
        and user.get("login") != agent_login
        and MARKER not in (comment.get("body") or "")
    )


def is_refined_feedback_candidate(
    labels: list[str],
    comments: list[dict],
    refined_at: str,
    agent_login: str = DEFAULT_AGENT_LOGIN,
) -> bool:
    label_set = {label.casefold() for label in labels}
    return (
        {"agent-refined", "ready-for-implementation"} <= label_set
        and "agent-ignore" not in label_set
        and bool(qualifying_comments(comments, refined_at, agent_login))
    )


def qualifying_comments(
    comments: list[dict], after: str | None = None, agent_login: str = DEFAULT_AGENT_LOGIN
) -> list[dict]: 
    cutoff = parse_timestamp(after) if after else None
    return [
        comment
        for comment in comments
        if is_qualifying_comment(comment, agent_login)
        and (cutoff is None or parse_timestamp(comment["created_at"]) > cutoff)
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comments", required=True)
    parser.add_argument("--after")
    parser.add_argument("--agent-login", default=DEFAULT_AGENT_LOGIN)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    comments = json.loads(Path(args.comments).read_text())
    eligible = qualifying_comments(comments, args.after, args.agent_login)
    Path(args.output).write_text(json.dumps(eligible))
    raise SystemExit(0 if eligible else 1)


if __name__ == "__main__":
    main()
