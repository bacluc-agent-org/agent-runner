#!/usr/bin/env python3
"""Advisory outbound HTTP guard for owned GitHub repositories.

Evaluation note: a stdlib guard was chosen over `nono`
(https://github.com/nolabs-ai/nono). `nono` provides kernel-level Landlock and
seccomp interception plus a CONNECT proxy, but it needs a pinned Rust binary and
profile, cannot tell an owned-repository mutation from an outsider one, and a
prior prototype denied legitimate `gh` GraphQL `POST /graphql` calls. Sandboxes
such as bwrap or firejail need privileges or daemons, and egress proxies need
TLS interception. This module is advisory: it has no production caller, is not
installed, and provides no kernel or socket enforcement.

Policy: GET, HEAD and OPTIONS are reads allowed to any valid http(s) URL. POST,
PUT, PATCH and DELETE are mutations allowed only over https on github.com,
api.github.com and uploads.github.com when the path names a repository in
bacluc, bacluc-agent or bacluc-agent-org. HEAD counts as a read for consistency
with the gh CLI wrapper. A denied outsider POST appends one JSON line to
$RUNNER_TEMP/http_guard_denials.jsonl (falling back to the system temp dir).
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit


OWNED_OWNERS = {"bacluc", "bacluc-agent", "bacluc-agent-org"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
GITHUB_MUTATION_HOSTS = {"github.com", "api.github.com", "uploads.github.com"}
LOG_NAME = "http_guard_denials.jsonl"


def _parse(url):
    try:
        parts = urlsplit(url)
    except ValueError as error:
        raise PermissionError(f"malformed URL: {error}") from error
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise PermissionError("URL scheme must be http or https")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise PermissionError("URL must not contain userinfo")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise PermissionError("URL must have a hostname")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url):
        raise PermissionError("URL must not contain control characters")
    path = parts.path or "/"
    if not path.startswith("/"):
        raise PermissionError("URL path must be absolute")
    lowered = path.lower()
    if "%2f" in lowered or "%5c" in lowered or "\\" in path:
        raise PermissionError("URL path must not contain encoded or raw separators")
    if "{" in path or "}" in path:
        raise PermissionError("URL path must not contain placeholders")
    if path == "/":
        segments = []
    else:
        if path.endswith("/"):
            raise PermissionError("URL path must not contain empty segments")
        segments = path[1:].split("/")
        if any(segment in {"", ".", ".."} for segment in segments):
            raise PermissionError("URL path must not contain empty or relative segments")
    try:
        port = parts.port
    except ValueError as error:
        raise PermissionError(f"invalid port: {error}") from error
    if port is not None and not 1 <= port <= 65535:
        raise PermissionError("URL port is invalid")
    return scheme, host, path, port, segments, parts.query


def _is_owned_target(host, segments):
    if host == "github.com":
        return len(segments) >= 2 and segments[0].lower() in OWNED_OWNERS
    if host in {"api.github.com", "uploads.github.com"}:
        return len(segments) >= 3 and segments[0] == "repos" and segments[1].lower() in OWNED_OWNERS
    return False


def _mutation_reason(scheme, host, port, segments, query):
    if query:
        return "mutation parameters must not be in the query string"
    if scheme != "https":
        return "mutations require https"
    if port not in {None, 443}:
        return "https mutations must use port 443"
    if host not in GITHUB_MUTATION_HOSTS:
        return "mutations are only allowed on GitHub"
    if host == "github.com":
        if len(segments) < 2:
            return "mutation path must be github.com/OWNER/REPO/..."
        owner = segments[0]
    else:
        if len(segments) < 3 or segments[0] != "repos":
            return "mutation path must be repos/OWNER/REPO/..."
        owner = segments[1]
    if owner.lower() not in OWNED_OWNERS:
        return "mutations are only allowed in bacluc, bacluc-agent or bacluc-agent-org repositories"
    return None


def _best_effort(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        path = parts.path or "/"
    except ValueError:
        return "", "/"
    return host, path


def _log_denial(method, host, path, reason):
    directory = os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "host": host,
        "path": path,
        "reason": reason,
    }
    try:
        with (Path(directory) / LOG_NAME).open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")
    except OSError as error:
        print(f"http_guard: could not write denial log: {error}", file=sys.stderr)


def check_request(method, url):
    method = method.upper()
    if method not in READ_METHODS | MUTATION_METHODS:
        raise PermissionError(f"unsupported HTTP method {method}")
    try:
        scheme, host, path, port, segments, query = _parse(url)
    except PermissionError as error:
        if method == "POST":
            _log_denial(method, *_best_effort(url), str(error))
        raise
    if method in READ_METHODS:
        return None
    reason = _mutation_reason(scheme, host, port, segments, query)
    if reason:
        if method == "POST" and not _is_owned_target(host, segments):
            _log_denial(method, host, path, reason)
        raise PermissionError(reason)
    return None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: http_guard.py METHOD URL", file=sys.stderr)
        return 2
    method, url = args
    try:
        check_request(method, url)
    except PermissionError as error:
        print(f"DENY: {error}", file=sys.stderr)
        return 1
    print(f"ALLOW {method.upper()} {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
