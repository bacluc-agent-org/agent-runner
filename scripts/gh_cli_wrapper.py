#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


PRIMARY_MARKS = (b"api rate limit exceeded", b"http 429")
SECONDARY_MARKS = (b"secondary rate limit", b"was submitted too quickly", b"abuse detection")


def deny(reason):
    raise ValueError(reason)


def owned_repo(repo):
    repo = re.sub(r"^(https://)?github\.com/", "", repo, flags=re.IGNORECASE)
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        deny("use an explicit github.com OWNER/REPO")
    owner, name = repo.split("/")
    if owner.lower() not in {"bacluc", "bacluc-agent", "bacluc-agent-org"} or name in {".", ".."}:
        deny("create the PR in the bacluc-agent fork, not the upstream repository")


def options(args, values, switches):
    positional, parsed = [], {}
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if not arg.startswith("-"):
            positional.append(arg)
            continue
        key, separator, value = arg.partition("=")
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            key, separator, value = arg[:2], "=", arg[2:].removeprefix("=")
        if key in values:
            if not separator:
                if index == len(args):
                    deny(f"missing value for {key}")
                value = args[index]
                index += 1
            parsed.setdefault(values[key], []).append(value)
        elif key in switches and (not separator or value in {"true", "false"}):
            continue
        else:
            deny(f"unsupported or ambiguous option {key}")
    return positional, parsed


def validate_pr(args):
    prefix = []
    while args and (args[0] in {"-R", "--repo"} or args[0].startswith(("-R", "--repo="))):
        flag, *args = args
        prefix.append(flag)
        if flag in {"-R", "--repo"}:
            if not args:
                deny("missing repository")
            value, *args = args
            prefix.append(value)
    if not args or args[0].startswith("-"):
        deny("use gh pr SUBCOMMAND followed by options")
    if args[0] == "revert":
        validate_revert(prefix, args)
        return
    if args[0] not in {"create", "new"}:
        return
    values = {key: "repo" for key in ("-R", "--repo")}
    values.update({key: key for key in (
        "-a", "--assignee", "--attach", "-B", "--base", "-b", "--body", "-F", "--body-file",
        "-H", "--head", "-l", "--label", "-m", "--milestone", "-p", "--project", "--recover",
        "-r", "--reviewer", "-T", "--template", "-t", "--title",
    )})
    positional, parsed = options(prefix + args[1:], values, {
        "-d", "--draft", "--dry-run", "-e", "--editor", "-f", "--fill", "--fill-first",
        "--fill-verbose", "--no-maintainer-edit", "-w", "--web", "--help",
    })
    if positional or len(parsed.get("repo", [])) != 1:
        deny("PR creation requires exactly one explicit -R/--repo destination")
    owned_repo(parsed["repo"][0])


def validate_revert(prefix, args):
    values = {key: "repo" for key in ("-R", "--repo")}
    values.update({key: key for key in ("-b", "--body", "-F", "--body-file", "-t", "--title")})
    positional, parsed = options(prefix + args[1:], values, {"-d", "--draft", "--help"})
    if len(positional) != 1:
        deny("PR revert requires exactly one PR number or github.com PR URL")
    repos = parsed.get("repo", [])
    if len(repos) > 1:
        deny("PR revert requires exactly one explicit -R/--repo destination")
    selector = positional[0]
    match = re.fullmatch(r"https?://github\.com/([^/]+/[^/]+)/pull/[0-9]+", selector, flags=re.IGNORECASE)
    url_repo = match.group(1) if match else None
    if repos:
        owned_repo(repos[0])
    if url_repo:
        owned_repo(url_repo)
    if not repos and not url_repo:
        deny("PR revert requires an explicit -R/--repo destination or a github.com PR URL")


def validate_api(args):
    values = {key: key for key in ("--cache", "-q", "--jq", "-p", "--preview", "-t", "--template")}
    for name, flags in {
        "method": ("-X", "--method"), "host": ("--hostname",),
        "body": ("-f", "--raw-field", "-F", "--field", "--input"),
        "header": ("-H", "--header"),
    }.items():
        values.update(dict.fromkeys(flags, name))
    positional, parsed = options(args, values, {
        "-i", "--include", "--paginate", "--silent", "--slurp", "--verbose",
        "--help", "--allow-escape-sequences",
    })
    if len(positional) != 1 or len(parsed.get("method", [])) > 1 or len(parsed.get("host", [])) > 1:
        deny("API calls require one unambiguous endpoint, method and host")
    if parsed.get("host", ["github.com"])[0].lower() != "github.com":
        deny("only github.com is supported")
    for header in parsed.get("header", []):
        if header.partition(":")[0].lower() not in {
            "accept", "authorization", "content-type", "x-github-api-version", "user-agent",
        }:
            deny("unsupported API header; routing overrides are not allowed")
    method = parsed.get("method", ["POST" if parsed.get("body") else "GET"])[0]
    if method not in {"GET", "HEAD", "OPTIONS", "POST", "PATCH", "PUT", "DELETE"}:
        deny("unsupported HTTP method")
    endpoint = positional[0]
    if endpoint.startswith("https://api.github.com/"):
        endpoint = endpoint.removeprefix("https://api.github.com/")
    else:
        endpoint = endpoint.removeprefix("/")
    path, query, _ = endpoint.partition("?")
    path = path.rstrip("/")
    if path.lower() == "graphql":
        deny("direct GraphQL is disabled; use guarded gh pr create -R or REST instead")
    if not re.fullmatch(r"[A-Za-z0-9_{}.-]+(?:/[A-Za-z0-9_{}.-]+)*", path) or any(
        part in {".", ".."} for part in path.split("/")
    ):
        deny("use a canonical github.com REST path without encoding or routing overrides")
    if method not in {"GET", "HEAD", "OPTIONS"}:
        if query:
            deny("put mutation parameters in fields, not the endpoint URL")
        parts = path.split("/")
        if parts[-1].lower() == "pulls":
            if len(parts) != 4 or parts[0] != "repos" or parts[3] != "pulls":
                deny("PR creation requires the canonical repos/OWNER/REPO/pulls endpoint")
            owned_repo("/".join(parts[1:3]))
        elif parts[-1].lower() == "reverts":
            if len(parts) != 6 or parts[0] != "repos" or parts[3].lower() != "pulls":
                deny("PR revert requires the canonical repos/OWNER/REPO/pulls/N/reverts endpoint")
            owned_repo("/".join(parts[1:3]))


def max_attempts():
    # ponytail: attempts are the only knob; a step that trips both the primary and the
    # secondary limit waits the sum of both — upgrade path: raise the job timeout.
    try:
        return max(1, int(os.environ["GH_CLI_WRAPPER_MAX_ATTEMPTS"]))
    except (KeyError, ValueError):
        return 4


def core_quota(backend):
    """(remaining, reset epoch seconds) from the free `<backend> api rate_limit`, or None."""
    probe = subprocess.run([backend, "api", "rate_limit"], capture_output=True)
    try:
        core = json.loads(probe.stdout)["resources"]["core"]
        return core["remaining"], core["reset"]
    except (ValueError, KeyError, TypeError):
        return None


def retry_delay(stderr, backend, attempt):
    """Seconds to sleep before the next attempt, or None when this is not a rate limit.

    ONE free `<backend> api rate_limit` probe supplies the reset; for a bare
    HTTP 403 that same probe is what confirms the quota is the cause.
    """
    marks = stderr.lower()
    if any(mark in marks for mark in SECONDARY_MARKS):
        return min(60, 5 * 2 ** (attempt - 1))
    proven = any(mark in marks for mark in PRIMARY_MARKS)
    if not proven and b"http 403" not in marks:
        return None
    quota = core_quota(backend)
    if quota is None or (not proven and quota[0] != 0):
        return None
    return max(0, int(quota[1] or 0) - int(time.time())) + 2


def run(backend, args):
    """Run the real CLI, retrying rate limits; stdout and stderr are emitted once, at the end."""
    stdin = {} if sys.stdin is None or sys.stdin.isatty() else {"input": sys.stdin.buffer.read()}
    limit = max_attempts()
    for attempt in range(1, limit + 1):
        proc = subprocess.run([backend, *args], capture_output=True, **stdin)
        if proc.returncode == 0:
            break
        wait = retry_delay(proc.stderr, backend, attempt)
        if wait is None:
            break
        if attempt == limit:
            print(f"::warning::gh cli wrapper: {' '.join(args[:2])} rate limited after "
                  f"{attempt}/{limit} attempts", file=sys.stderr)
            break
        time.sleep(wait)
    # ponytail: only the final attempt's bytes reach the caller, so a jq consumer
    # never sees duplicated or partial output; upgrade path: tee when progress matters.
    sys.stdout.buffer.write(proc.stdout)
    sys.stderr.buffer.write(proc.stderr)
    return proc.returncode


def main():
    backend = Path(__file__).resolve().with_name("real-gh").read_text().strip()
    if not os.path.isabs(backend) or not os.access(backend, os.X_OK) or Path(backend).resolve() == Path(__file__).resolve():
        deny("real gh backend is missing or recursive; reinstall the guard")
    args = sys.argv[1:]
    if not args or args[0] not in {
        "api", "auth", "browse", "cache", "codespace", "completion", "config", "gist", "gpg-key",
        "help", "issue", "label", "org", "pr", "project", "release", "repo", "ruleset", "run",
        "search", "secret", "ssh-key", "status", "variable", "workflow", "version", "--version", "--help",
    }:
        deny("only builtin commands are supported; aliases and extensions are disabled")
    aliases = subprocess.run([backend, "alias", "list"], capture_output=True, text=True)
    if aliases.returncode:
        deny("cannot check configured gh aliases")
    if any(line.split(":", 1)[0].strip() == args[0] for line in aliases.stdout.splitlines()):
        deny("configured alias execution is disabled; use the builtin command explicitly")
    if os.environ.get("GH_HOST", "github.com").lower() != "github.com":
        deny("only github.com is supported")
    if args[0] == "pr":
        validate_pr(args[1:])
    elif args[0] == "api":
        validate_api(args[1:])
    rc = run(backend, args)
    sys.exit(128 - rc if rc < 0 else rc)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"gh cli wrapper: {error}", file=sys.stderr)
        sys.exit(1)
