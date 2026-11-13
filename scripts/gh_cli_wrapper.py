#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


STREAMING_COMMANDS = {
    "auth", "browse", "cache", "codespace", "completion", "config", "gist", "gpg-key", "help",
    "org", "project", "ruleset", "secret", "ssh-key", "variable", "version", "--help", "--version",
}
STREAMING_PREFIXES = {
    ("pr", "create"), ("pr", "new"), ("pr", "edit"), ("pr", "merge"), ("pr", "close"),
    ("pr", "reopen"), ("pr", "checkout"), ("pr", "revert"),
    ("issue", "create"),
    ("release", "create"), ("release", "edit"), ("release", "upload"), ("release", "delete"),
    ("run", "watch"), ("run", "cancel"), ("run", "rerun"), ("run", "download"),
    ("workflow", "run"), ("workflow", "enable"), ("workflow", "disable"),
}
STREAMING_FLAGS = {"--web", "--watch", "--log-follow", "--editor"}


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
    return path


def env_int(name, default, minimum):
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def streaming(args):
    return (
        not args
        or args[0] in STREAMING_COMMANDS
        or tuple(args[:2]) in STREAMING_PREFIXES
        or any(arg in STREAMING_FLAGS for arg in args)
    )


def quota(backend, bucket):
    try:
        result = subprocess.run(
            [backend, "api", "rate_limit"], capture_output=True, text=True, timeout=5,
        )
        if result.returncode:
            return None
        resource = json.loads(result.stdout)["resources"][bucket]
        return {"remaining": int(resource["remaining"]), "reset": int(resource["reset"])}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def bucket_for(args, endpoint, stderr):
    if "graphql:" in stderr.lower():
        return "graphql"
    if args[0] == "search":
        return "search"
    if args[0] == "api":
        path = (endpoint or "").lower()
        return "search" if path == "search" or path.startswith("search/") else "core"
    if tuple(args[:2]) in {
        ("issue", "view"), ("issue", "edit"), ("pr", "view"), ("pr", "list"),
        ("pr", "checks"), ("repo", "view"),
    }:
        return "graphql"
    return "core"


def classify(stderr):
    text = stderr.lower()
    if any(token in text for token in (
        "http 429", "secondary rate limit", "abuse detection", "was submitted too quickly",
        "retry-after",
    )):
        return "secondary"
    if "api rate limit exceeded" in text:
        return "primary"
    return None


def retry_delay(backend, bucket, limit, attempt, cap):
    if limit == "secondary":
        return min(cap, 5 * 2 ** (attempt - 1))
    probe = quota(backend, bucket)
    if probe is None:
        print(f"::warning::gh cli wrapper: could not read {bucket} rate limit; using exponential backoff",
              file=sys.stderr)
        return min(cap, 5 * 2 ** (attempt - 1))
    if probe["remaining"] == 0:
        reset = probe["reset"]
        delay = max(0, reset - int(time.time())) + 2
        if delay > cap:
            print(f"::warning::gh cli wrapper: {bucket} primary rate limit resets at {reset}, "
                  f"wait {delay}s exceeds the {cap}s cap", file=sys.stderr)
            return None
        return delay
    return min(cap, 5 * 2 ** (attempt - 1))


def silence_broken_pipe():
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        for stream in (sys.stdout, sys.stderr):
            try:
                os.dup2(devnull, stream.fileno())
            except (OSError, ValueError):
                pass
        os.close(devnull)
    except OSError:
        pass


def emit(result):
    try:
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        silence_broken_pipe()
    try:
        sys.stderr.buffer.write(result.stderr)
        sys.stderr.buffer.flush()
    except BrokenPipeError:
        silence_broken_pipe()
    return 128 - result.returncode if result.returncode < 0 else result.returncode


def run_with_retries(backend, args, endpoint):
    max_attempts = env_int("GH_CLI_WRAPPER_MAX_ATTEMPTS", 4, 1)
    cap = env_int("GH_CLI_WRAPPER_MAX_SLEEP_SECONDS", 600, 0)
    stdin = None
    if not sys.stdin.isatty():
        try:
            stdin = sys.stdin.buffer.read()
        except (OSError, ValueError):
            stdin = b""
    label = " ".join(args[:2])
    final = None
    for attempt in range(1, max_attempts + 1):
        options = {"capture_output": True}
        if stdin is not None:
            options["input"] = stdin
        final = subprocess.run([backend, *args], **options)
        limit = classify(final.stderr.decode("utf-8", "replace"))
        if limit is None:
            break
        if attempt >= max_attempts:
            print(f"::warning::gh cli wrapper: gh {label} still rate limited after {max_attempts} attempts",
                  file=sys.stderr)
            break
        bucket = bucket_for(args, endpoint, final.stderr.decode("utf-8", "replace"))
        delay = retry_delay(backend, bucket, limit, attempt, cap)
        if delay is None:
            break
        print(f"::notice::gh cli wrapper: retrying {label} in {delay}s "
              f"(attempt {attempt}/{max_attempts}, {bucket} {limit} limit)", file=sys.stderr)
        if delay:
            time.sleep(delay)
    return emit(final)


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
    endpoint = validate_api(args[1:]) if args[0] == "api" else None
    if streaming(args):
        os.execv(backend, [backend, *args])
    sys.exit(run_with_retries(backend, args, endpoint))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"gh cli wrapper: {error}", file=sys.stderr)
        sys.exit(1)
