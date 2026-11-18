#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


RETRYABLE = re.compile(r"API rate limit (?:already )?exceeded|secondary rate limit|abuse detection|HTTP 429", re.I)
SECONDARY = re.compile(r"HTTP 429|secondary rate limit|abuse detection", re.I)


def deny(reason):
    raise ValueError(reason)


def env_int(name, default, minimum):
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


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
        validate_graphql(parsed)
        return "graphql"
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


def validate_graphql(parsed):
    fields = parsed.get("body", [])
    queries = [field.partition("=")[2] for field in fields if field.startswith("query=")]
    if len(queries) != 1 or any(
        not field.partition("=")[0] in {
            "query", "owner", "login", "number", "projectId", "itemId", "fieldId",
            "afterId", "singleSelectOptionId",
        }
        for field in fields
    ):
        deny("GraphQL requires a project-v2 query")
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", queries[0]))
    allowed = {
        "ProjectV2", "ProjectV2FieldConfiguration", "ProjectV2Item",
        "ProjectV2SingleSelectField", "organization", "projectV2", "projectV2Items",
        "updateProjectV2ItemFieldValue", "updateProjectV2ItemPosition", "query", "mutation",
        "node", "id", "number", "after", "afterId", "first", "last", "field", "fields", "input",
        "items", "nodes", "pageInfo", "hasNextPage", "endCursor", "position", "fieldId",
        "singleSelectOptionId", "projectId", "itemId", "organizationId", "owner", "login",
        "cursor", "value", "clientMutationId", "projectV2Item", "null", "true", "false",
        "Int", "String", "ID",
    }
    if not tokens or not tokens <= allowed:
        deny("only project-v2 GraphQL operations are supported")


def streaming(args):
    if any(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        return True
    if not args:
        return True
    if args[0] in {"auth", "browse", "config", "help", "version", "--help", "--version"}:
        return True
    if tuple(args[:2]) == ("run", "watch"):
        return True
    return any(token.partition("=")[0] in {"--web", "--watch", "--log-follow", "--editor"} for token in args)


def needs_stdin(args):
    return any(arg == "-" or arg.endswith("=-") or arg.endswith("@-") for arg in args)


def classify(stderr):
    text = stderr.decode(errors="replace")
    if not RETRYABLE.search(text):
        return None
    return "secondary" if SECONDARY.search(text) else "primary"


def bucket_for(args, endpoint, stderr):
    text = stderr.decode(errors="replace").lower()
    if "graphql" in text:
        return "graphql"
    if "search" in text:
        return "search"
    if args and args[0] == "search":
        return "search"
    if endpoint == "graphql":
        return "graphql"
    if endpoint and (endpoint == "search" or endpoint.startswith("search/")):
        return "search"
    return {
        ("issue", "view"): "graphql",
        ("issue", "edit"): "graphql",
        ("pr", "view"): "graphql",
        ("pr", "list"): "graphql",
        ("pr", "checks"): "graphql",
        ("repo", "view"): "graphql",
        ("label", "list"): "graphql",
        ("label", "create"): "core",
    }.get(tuple(args[:2]))


def quota(backend):
    try:
        result = subprocess.run([backend, "api", "rate_limit"], capture_output=True, text=True, timeout=5)
        if result.returncode:
            return None
        resources = json.loads(result.stdout).get("resources")
        return resources if isinstance(resources, dict) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def backoff(attempt, cap):
    return min(cap, 5 * 2 ** (attempt - 1))


def reset_wait(resources):
    return max(0, max((resource.get("reset", 0) for resource in resources), default=0) - time.time())


def retry_delay(backend, bucket, kind, attempt, cap):
    if kind == "secondary":
        return backoff(attempt, cap)
    resources = quota(backend)
    if resources is None:
        print("::warning::gh cli wrapper: cannot read the GitHub rate limit; using exponential backoff", file=sys.stderr)
        return backoff(attempt, cap)
    known = bucket in {"core", "search", "graphql"} and isinstance(resources.get(bucket), dict)
    if known:
        resource = resources[bucket]
        if resource.get("remaining", 1) <= 0:
            delay = reset_wait([resource])
            if delay > cap:
                print(f"::warning::gh cli wrapper: {bucket} rate limit resets in {delay}s, beyond the {cap}s retry cap", file=sys.stderr)
                return None
            return delay
        return backoff(attempt, cap)
    depleted = [
        resource for name in ("core", "search", "graphql")
        if isinstance(resource := resources.get(name), dict) and resource.get("remaining", 1) <= 0
    ]
    if depleted:
        delay = reset_wait(depleted)
        if delay > cap:
            print(f"::warning::gh cli wrapper: rate limit resets in {delay}s, beyond the {cap}s retry cap", file=sys.stderr)
            return None
        return delay
    return backoff(attempt, cap)


def pause(seconds):
    override = os.environ.get("GH_CLI_WRAPPER_SLEEP_SECONDS")
    try:
        time.sleep(float(override) if override is not None else seconds)
    except ValueError:
        time.sleep(seconds)


def emit(result):
    try:
        sys.stdout.buffer.write(result.stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(result.stderr)
        sys.stderr.buffer.flush()
    except BrokenPipeError:
        pass
    return 128 - result.returncode if result.returncode < 0 else result.returncode


def run_with_retries(backend, args, endpoint):
    attempts = env_int("GH_CLI_WRAPPER_MAX_ATTEMPTS", 3, 1)
    cap = env_int("GH_CLI_WRAPPER_MAX_SLEEP_SECONDS", 120, 0)
    stdin_data = None
    if needs_stdin(args) and not sys.stdin.isatty():
        stdin_data = sys.stdin.buffer.read()
    for attempt in range(1, attempts + 1):
        result = subprocess.run([backend, *args], input=stdin_data, capture_output=True)
        kind = classify(result.stderr)
        if result.returncode == 0 or kind is None:
            return emit(result)
        if attempt == attempts:
            print(f"::warning::gh cli wrapper: gh {' '.join(args[:2])} still rate limited after {attempts} attempts", file=sys.stderr)
            return emit(result)
        bucket = bucket_for(args, endpoint, result.stderr)
        delay = retry_delay(backend, bucket, kind, attempt, cap)
        if delay is None:
            return emit(result)
        print(
            f"::notice::gh cli wrapper: retrying gh {' '.join(args[:2])} in {delay}s "
            f"(attempt {attempt}/{attempts}, {bucket or 'unknown'} {kind} limit)",
            file=sys.stderr,
        )
        pause(delay)


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
    endpoint = None
    if args[0] == "pr":
        validate_pr(args[1:])
    elif args[0] == "api":
        endpoint = validate_api(args[1:])
    if streaming(args):
        os.execv(backend, [backend, *args])
    sys.exit(run_with_retries(backend, args, endpoint))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"gh cli wrapper: {error}", file=sys.stderr)
        sys.exit(1)
