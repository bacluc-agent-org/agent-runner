#!/usr/bin/env python3
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from urllib.parse import urlparse

OWNED_ORGS = {"bacluc", "bacluc-agent", "bacluc-agent-org"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
ALLOWED_HOSTS = {"github.com", "api.github.com", "uploads.github.com"}
FORK_PATH = re.compile(r"^/repos/[^/]+/[^/]+/forks/?$", flags=re.IGNORECASE)
NONO_DENIAL = re.compile(
    r"(?i)(?P<reason>.*\bden(?:y|ied|ial)\b.*?)(?P<method>GET|HEAD|OPTIONS|POST|PUT|PATCH|DELETE|CONNECT)\s+(?P<target>\S+)"
)
NONO_DENIAL_TARGET = re.compile(
    r"(?i)(?P<method>GET|HEAD|OPTIONS|POST|PUT|PATCH|DELETE|CONNECT)\s+(?P<target>https?://\S+|/\S+)"
)


def _deny(method, url, reason):
    parsed = urlparse(url)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "host": (parsed.hostname or "").lower(),
        "path": parsed.path,
        "reason": reason,
    }
    log = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "http-guard-denials.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    raise PermissionError(f"http guard denied {method} {url}: {reason}")


def check_request(method, url):
    method = method.upper()
    if method in READ_METHODS:
        return
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in ALLOWED_HOSTS:
        _deny(method, url, "host not allowlisted")
    if method == "POST" and FORK_PATH.fullmatch(parsed.path):
        return
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) < 3
        or parts[0].lower() != "repos"
        or parts[1].lower() not in OWNED_ORGS
        or parts[2] in {".", ".."}
    ):
        _deny(method, url, "mutation outside owned repositories")


def parse_nono_diagnostic(line):
    if not re.search(r"(?i)\bden(?:y|ied|ial)\b", line):
        return None
    match = NONO_DENIAL.search(line) or NONO_DENIAL_TARGET.search(line)
    if not match:
        return None
    target = match.group("target").rstrip(".,;)")
    parsed = urlparse(target)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": match.group("method").upper(),
        "host": (parsed.hostname or "").lower(),
        "path": parsed.path or target,
        "reason": line.rstrip("\n"),
    }


def parse_nono_stderr(lines, output):
    for line in lines:
        entry = parse_nono_diagnostic(line)
        if entry:
            output.write(json.dumps(entry) + "\n")


if __name__ == "__main__" and sys.argv[1:] == ["--parse-nono"]:
    log = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "http-guard-denials.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        parse_nono_stderr(sys.stdin, handle)
