#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def deny(reason):
    raise ValueError(reason)


def owned_repo(repo):
    repo = re.sub(r"^(https://)?github\.com/", "", repo, flags=re.IGNORECASE)
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        deny("use an explicit github.com OWNER/REPO")
    owner, name = repo.split("/")
    if owner.lower() not in {"bacluc", "bacluc-agent"} or name in {".", ".."}:
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


READONLY_COMMANDS = {
    ("issue", "view"): "issue_view",
    ("pr", "view"): "pr_view",
    ("pr", "list"): "pr_list",
    ("repo", "view"): "repo_view",
    ("repo", "list"): "repo_list",
}

READONLY_FIELDS = {
    "issue_view": {"number", "title", "body", "state", "url", "createdAt", "updatedAt", "comments"},
    "pr_view": {"number", "title", "body", "state", "url", "createdAt", "updatedAt", "headRefName",
                "comments", "reviews"},
    "pr_list": {"number", "title", "url", "headRefName", "state", "createdAt", "updatedAt"},
    "repo_view": {"nameWithOwner", "url", "description"},
    "repo_list": {"nameWithOwner"},
}

CANONICAL_FIELDS = {
    "number": "number", "title": "title", "body": "body", "state": "state", "url": "url",
    "description": "description", "createdat": "createdAt", "updatedat": "updatedAt",
    "comments": "comments", "reviews": "reviews", "headrefname": "headRefName",
    "namewithowner": "nameWithOwner",
}


def readonly_repo(value):
    value = re.sub(r"^(https://)?github\.com/", "", value, flags=re.IGNORECASE)
    owner, _, name = value.partition("/")
    if not re.fullmatch(r"[A-Za-z0-9-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    if name in {".", ".."}:
        return None
    return f"{owner}/{name}"


def readonly_recipe(args):
    kind = READONLY_COMMANDS.get((args[0], args[1])) if len(args) >= 2 else None
    if kind is None:
        return None
    recipe = {"kind": kind, "fields": [], "unmapped": [], "repo": None, "selector": None,
              "limit": None, "state": None, "head": None, "expr": None}
    index = 2
    while index < len(args):
        arg = args[index]
        index += 1
        if not arg.startswith("-"):
            if recipe["selector"] is not None:
                return None
            recipe["selector"] = arg
            continue
        key, separator, value = arg.partition("=")
        if not arg.startswith("--") and len(arg) > 2:
            key, separator, value = arg[:2], "=", arg[2:].removeprefix("=")
        if key in {"-R", "--repo"}:
            if not separator:
                if index == len(args):
                    return None
                value = args[index]
                index += 1
            if recipe["repo"] is not None:
                return None
            recipe["repo"] = value
        elif key == "--json":
            if recipe["fields"] or recipe["unmapped"]:
                return None
            if not separator:
                if index == len(args):
                    return None
                value = args[index]
                index += 1
            for name in value.split(","):
                canonical = CANONICAL_FIELDS.get(name.lower())
                if canonical is None or canonical not in READONLY_FIELDS[kind]:
                    recipe["unmapped"].append(name)
                else:
                    recipe["fields"].append(canonical)
        elif key in {"--jq", "-q"}:
            if not separator:
                if index == len(args):
                    return None
                value = args[index]
                index += 1
            if recipe["expr"] is not None:
                return None
            recipe["expr"] = value
        elif kind in {"pr_list", "repo_list"} and key in {"-L", "--limit"}:
            if not separator:
                if index == len(args):
                    return None
                value = args[index]
                index += 1
            if not value.isdigit() or recipe["limit"] is not None:
                return None
            recipe["limit"] = int(value)
        elif kind == "pr_list" and key == "--state":
            if not separator:
                if index == len(args):
                    return None
                value = args[index]
                index += 1
            if value not in {"open", "closed", "all", "merged"} or recipe["state"] is not None:
                return None
            recipe["state"] = value
        elif kind == "pr_list" and key == "--head":
            if not separator:
                if index == len(args):
                    return None
                value = args[index]
                index += 1
            if recipe["head"] is not None or not value:
                return None
            recipe["head"] = value.split(":")[-1]
        else:
            return None
    if kind in {"issue_view", "pr_view", "pr_list"}:
        recipe["repo"] = readonly_repo(recipe["repo"] or "")
        if recipe["repo"] is None or (
            kind != "pr_list" and not re.fullmatch(r"[0-9]+", recipe["selector"] or "")
        ):
            return None
    elif kind == "repo_view":
        recipe["repo"] = readonly_repo(recipe["selector"] or "")
        if recipe["repo"] is None:
            return None
    elif not re.fullmatch(r"[A-Za-z0-9-]+", recipe["selector"] or ""):
        return None
    if kind in {"pr_list", "repo_list"} and recipe["limit"] is None:
        recipe["limit"] = 30
    if kind == "pr_list" and recipe["state"] is None:
        recipe["state"] = "open"
    if not recipe["fields"] and not recipe["unmapped"]:
        return None
    return recipe


def rest_call(backend, endpoint):
    result = subprocess.run([backend, "api", endpoint], capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        print(f"gh cli wrapper: {endpoint} REST fallback failed (status {result.returncode})", file=sys.stderr)
        raise SystemExit(1)
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        print(f"gh cli wrapper: {endpoint} REST fallback failed ({error})", file=sys.stderr)
        raise SystemExit(1)


def collection(backend, base, params, limit):
    items = []
    for page in range(1, 21):
        query = "&".join([*params, "per_page=100", f"page={page}"])
        batch = rest_call(backend, f"{base}?{query}")
        if not isinstance(batch, list):
            raise ValueError("list response expected")
        items.extend(batch)
        if len(batch) < 100 or (limit is not None and len(items) >= limit):
            break
    return items


def base_doc(rest, fields, state):
    head = rest.get("head") or {}
    grabbed = {"number": rest.get("number"), "title": rest.get("title"), "body": rest.get("body"),
               "description": rest.get("description"), "url": rest.get("html_url"),
               "createdAt": rest.get("created_at"), "updatedAt": rest.get("updated_at"),
               "headRefName": head.get("ref"), "nameWithOwner": rest.get("full_name"), "state": state}
    return {field: grabbed[field] for field in fields}


def pull_state(rest):
    merged = rest.get("merged_at")
    if merged and merged != "null":
        return "MERGED"
    return "OPEN" if rest.get("state") == "open" else "CLOSED"


def comments_doc(items):
    return [{"author": {"login": item["user"]["login"]}, "body": item.get("body"),
             "createdAt": item.get("created_at")} for item in items]


def reviews_doc(items):
    return [{"author": {"login": item["user"]["login"]}, "submittedAt": item.get("submitted_at")}
            for item in items]


def build_doc(backend, recipe):
    kind = recipe["kind"]
    if kind == "issue_view":
        issue = f"repos/{recipe['repo']}/issues/{recipe['selector']}"
        rest = rest_call(backend, issue)
        doc = base_doc(rest, [field for field in recipe["fields"] if field != "comments"],
                       "OPEN" if rest["state"] == "open" else "CLOSED")
        if "comments" in recipe["fields"]:
            doc["comments"] = comments_doc(collection(backend, f"{issue}/comments", [], None))
        return doc
    if kind == "pr_view":
        pull = f"repos/{recipe['repo']}/pulls/{recipe['selector']}"
        rest = rest_call(backend, pull)
        doc = base_doc(rest, [field for field in recipe["fields"] if field not in ("comments", "reviews")],
                       pull_state(rest))
        if "comments" in recipe["fields"]:
            doc["comments"] = comments_doc(
                collection(backend, f"repos/{recipe['repo']}/issues/{recipe['selector']}/comments", [], None))
        if "reviews" in recipe["fields"]:
            doc["reviews"] = reviews_doc(collection(backend, f"{pull}/reviews", [], None))
        return doc
    if kind == "pr_list":
        state = "all" if recipe["state"] == "merged" else recipe["state"]
        items = collection(backend, f"repos/{recipe['repo']}/pulls", [f"state={state}"], recipe["limit"])
        if recipe["head"]:
            items = [item for item in items if (item.get("head") or {}).get("ref") == recipe["head"]]
        if recipe["state"] == "merged":
            items = [item for item in items if item.get("merged_at")]
        return [base_doc(item, recipe["fields"], pull_state(item)) for item in items[:recipe["limit"]]]
    if kind == "repo_view":
        return base_doc(rest_call(backend, f"repos/{recipe['repo']}"), recipe["fields"], None)
    return [{"nameWithOwner": item["full_name"]}
            for item in collection(backend, f"users/{recipe['selector']}/repos", [], recipe["limit"])
            [:recipe["limit"]]]


def emit_doc(doc, recipe):
    if recipe["expr"] is None:
        sys.stdout.write(json.dumps(doc, indent=2) + "\n")
        return 0
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    try:
        json.dump(doc, handle)
    finally:
        handle.close()
    try:
        jq = subprocess.run(["jq", "-r", recipe["expr"], handle.name], capture_output=True, text=True)
        if jq.returncode != 0:
            sys.stderr.write(jq.stderr)
            print(f"gh cli wrapper: [{recipe['kind']}] fallback jq failed (status {jq.returncode})", file=sys.stderr)
            return 1
    except OSError as error:
        print(f"gh cli wrapper: [{recipe['kind']}] fallback jq failed ({error})", file=sys.stderr)
        return 1
    finally:
        os.unlink(handle.name)
    sys.stdout.write(jq.stdout)
    return 0


def run_fallback(backend, args, recipe):
    result = subprocess.run([backend, *args], capture_output=True, text=True)
    if result.returncode == 0 or not (result.returncode != 0 and "HTTP 403" in result.stderr
                                      and "api.github.com/graphql" in result.stderr):
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    if recipe["unmapped"]:
        if recipe["kind"] == "issue_view":
            hint = f"gh api repos/{recipe['repo']}/issues/{recipe['selector']}"
        elif recipe["kind"] == "pr_view":
            hint = f"gh api repos/{recipe['repo']}/pulls/{recipe['selector']}"
        elif recipe["kind"] == "pr_list":
            hint = f"gh api repos/{recipe['repo']}/pulls"
        elif recipe["kind"] == "repo_view":
            hint = f"gh api repos/{recipe['repo']}"
        else:
            hint = f"gh api users/{recipe['selector']}/repos"
        print(f"gh cli wrapper: --json field '{recipe['unmapped'][0]}' has no REST fallback; "
              f"use a smaller field list or raw REST ({hint}) or run outside the sandbox", file=sys.stderr)
        return 1
    try:
        doc = build_doc(backend, recipe)
    except (KeyError, TypeError, ValueError) as error:
        print(f"gh cli wrapper: fallback REST response shape unexpected for {recipe['kind']}: {error}",
              file=sys.stderr)
        return 1
    return emit_doc(doc, recipe)


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
    recipe = readonly_recipe(args)
    if not recipe:
        os.execv(backend, [backend, *args])
    sys.exit(run_fallback(backend, args, recipe))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"gh cli wrapper: {error}", file=sys.stderr)
        sys.exit(1)
