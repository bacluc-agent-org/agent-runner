#!/usr/bin/env python3
"""Record a model failure into the model-availability cache issue."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

GITHUB_ISSUE_BODY_LIMIT = 65536
KNOWN_REASONS = {"balance", "auth", "model_unavailable", "retry_limit"}
PROVIDER_SCOPED_REASONS = {"balance", "auth"}


def run_gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def issue_repo() -> str:
    return os.environ.get("ISSUE_REPOSITORY", "")


def repo_flag() -> list[str]:
    repo = issue_repo().strip()
    return ["-R", repo] if repo else []


def read_cache(cache_issue) -> dict:
    try:
        body = run_gh(
            "issue", "view", str(cache_issue), *repo_flag(), "--json", "body", "--jq", ".body"
        )
        cache = json.loads(body)
        return cache if isinstance(cache, dict) else {}
    except Exception:
        return {}


def write_cache(cache_issue, cache: dict) -> None:
    body = json.dumps(cache)
    if len(body.encode("utf-8")) > GITHUB_ISSUE_BODY_LIMIT:
        print(
            f"warning: cache body is {len(body.encode('utf-8'))} bytes, exceeding the "
            f"{GITHUB_ISSUE_BODY_LIMIT}-byte GitHub issue limit; cache not updated",
            file=sys.stderr,
        )
        return
    try:
        run_gh("issue", "edit", str(cache_issue), *repo_flag(), "--body", body)
    except Exception as e:
        print(
            f"warning: failed to write cache issue {cache_issue}: {e}: {getattr(e, 'stderr', '') or ''}",
            file=sys.stderr,
        )


def record_failure(cache, model, reason, checked) -> dict:
    if not isinstance(cache, dict):
        return {}
    if not model or reason not in KNOWN_REASONS:
        return dict(cache)
    entry = {"ok": False, "checked": checked, "reason": reason}
    result = dict(cache)
    provider = model.split("/", 1)[0] if "/" in model else model
    if reason in PROVIDER_SCOPED_REASONS:
        for key in list(result.keys()):
            key_provider = key.split("/", 1)[0] if "/" in key else key
            if key_provider == provider:
                result[key] = dict(entry)
        if model not in result:
            result[model] = dict(entry)
    else:
        result[model] = dict(entry)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="")
    parser.add_argument("--cache-issue", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--checked", default="")
    args = parser.parse_args(argv)

    model = args.model or ""
    reason = args.reason or ""
    if not model or not reason or reason not in KNOWN_REASONS:
        return 0

    if args.repo:
        os.environ["ISSUE_REPOSITORY"] = args.repo

    checked = args.checked or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache_issue = args.cache_issue
    if not cache_issue:
        return 0

    try:
        cache = read_cache(cache_issue)
    except Exception as e:
        print(f"warning: failed to read cache issue {cache_issue}: {e}", file=sys.stderr)
        return 0

    new_cache = record_failure(cache, model, reason, checked)

    try:
        write_cache(cache_issue, new_cache)
    except Exception as e:
        print(f"warning: failed to write cache issue {cache_issue}: {e}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
