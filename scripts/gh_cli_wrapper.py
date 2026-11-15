#!/usr/bin/env python3
import os
from pathlib import Path
import re
import subprocess
import sys


OWNED_OWNERS = {"bacluc", "bacluc-agent", "bacluc-agent-org"}
TARGET_FLAGS = {"-R", "--repo"}


def deny(reason):
    raise ValueError(reason)


def owned_owner(owner):
    if owner.lower() not in OWNED_OWNERS:
        deny("use an owned bacluc, bacluc-agent or bacluc-agent-org account or repository")


def owned_repo(repo):
    repo = re.sub(r"^(https://)?github\.com/", "", repo, flags=re.IGNORECASE)
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        deny("use an explicit github.com OWNER/REPO")
    owner, name = repo.split("/")
    owned_owner(owner)
    if name in {".", ".."}:
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


def _target_flag(arg, target_flags):
    for flag in target_flags:
        if arg == flag or arg.startswith(flag + "="):
            return flag
        if not flag.startswith("--") and arg.startswith(flag) and len(arg) > len(flag):
            return flag
    return None


def split_target(args, target_flags=TARGET_FLAGS):
    prefix = []
    while args and args[0].startswith("-"):
        flag = _target_flag(args[0], target_flags)
        if flag is None:
            if args[0] in {"-h", "--help"} and len(args) == 1:
                return prefix, None, args
            deny("put the subcommand before flags")
        if args[0] == flag:
            prefix.append(flag)
            args = args[1:]
            if not args:
                deny(f"missing value for {flag}")
            prefix.append(args[0])
            args = args[1:]
        else:
            prefix.append(args[0])
            args = args[1:]
    if not args or args[0].startswith("-"):
        deny("use gh SUBCOMMAND followed by options")
    return prefix, args[0], args[1:]


def flags(*names):
    return {name: name for name in names}


REPO_FLAGS = {"-R": "repo", "--repo": "repo"}
ORG_FLAGS = {"-o": "org", "--org": "org"}

ISSUE_COMMANDS = {
    "create": ({**REPO_FLAGS, **flags(
        "-a", "--assignee", "--attach", "--blocked-by", "--blocking", "-b", "--body", "-F",
        "--body-file", "-l", "--label", "-m", "--milestone", "--parent", "-p", "--project",
        "--recover", "-T", "--template", "-t", "--title", "--type",
    )}, {"-e", "--editor", "-w", "--web"}),
    "comment": ({**REPO_FLAGS, **flags(
        "--attach", "-b", "--body", "-F", "--body-file",
    )}, {"--create-if-none", "--delete-last", "--edit-last", "-e", "--editor", "-w", "--web", "--yes"}),
    "edit": ({**REPO_FLAGS, **flags(
        "--add-assignee", "--add-blocked-by", "--add-blocking", "--add-label", "--add-project",
        "--add-sub-issue", "--attach", "-b", "--body", "-F", "--body-file", "-m", "--milestone",
        "--parent", "--remove-assignee", "--remove-blocked-by", "--remove-blocking",
        "--remove-label", "--remove-project", "--remove-sub-issue", "-t", "--title", "--type",
    )}, {"--remove-milestone", "--remove-parent", "--remove-type"}),
    "close": ({**REPO_FLAGS, **flags("-c", "--comment", "--duplicate-of", "-r", "--reason")}, set()),
    "reopen": ({**REPO_FLAGS, **flags("-c", "--comment")}, set()),
    "lock": ({**REPO_FLAGS, **flags("-r", "--reason")}, set()),
    "unlock": (REPO_FLAGS, set()),
    "transfer": (REPO_FLAGS, set()),
}

REPO_COMMANDS = {
    "fork": ({"--org": "org", **flags("--fork-name", "--remote-name")},
             {"--clone", "--default-branch-only", "--remote"}),
    "create": (flags(
        "-d", "--description", "-g", "--gitignore", "-h", "--homepage", "-l", "--license",
        "-r", "--remote", "-s", "--source", "-t", "--team", "-p", "--template",
    ), {"--add-readme", "-c", "--clone", "--disable-issues", "--disable-wiki",
        "--include-all-branches", "--internal", "--private", "--public", "--push"}),
    "delete": (REPO_FLAGS, {"--yes"}),
    "edit": ({**REPO_FLAGS, **flags(
        "-d", "--description", "-h", "--homepage", "--default-branch",
        "--squash-merge-commit-message", "--visibility", "--add-topic", "--remove-topic",
    )}, {"--accept-visibility-change-consequences", "--allow-forking", "--allow-update-branch",
         "--delete-branch-on-merge", "--enable-advanced-security", "--enable-auto-merge",
         "--enable-discussions", "--enable-issues", "--enable-merge-commit", "--enable-projects",
         "--enable-rebase-merge", "--enable-secret-scanning",
         "--enable-secret-scanning-push-protection", "--enable-squash-merge", "--enable-wiki",
         "--template"}),
    "archive": (REPO_FLAGS, {"-y", "--yes"}),
    "rename": (REPO_FLAGS, {"-y", "--yes"}),
}

WORKFLOW_COMMANDS = {
    "run": ({**REPO_FLAGS, **flags("-F", "--field", "-f", "--raw-field", "-r", "--ref")}, {"--json"}),
    "dispatch": ({**REPO_FLAGS, **flags("-F", "--field", "-f", "--raw-field", "-r", "--ref")}, {"--json"}),
    "enable": (REPO_FLAGS, set()),
    "disable": (REPO_FLAGS, set()),
}

DISCUSSION_COMMANDS = {
    "create": ({**REPO_FLAGS, **flags("-b", "--body", "-F", "--body-file", "-c", "--category",
                                       "-l", "--label", "-t", "--title")}, set()),
    "comment": ({**REPO_FLAGS, **flags("-b", "--body", "-F", "--body-file")},
                {"--delete", "--edit", "--yes"}),
    "edit": ({**REPO_FLAGS, **flags("--add-label", "-b", "--body", "-F", "--body-file",
                                     "-c", "--category", "--remove-label", "-t", "--title")}, set()),
}

LABEL_COMMANDS = {
    "create": ({**REPO_FLAGS, **flags("-c", "--color", "-d", "--description")}, {"-f", "--force"}),
    "edit": ({**REPO_FLAGS, **flags("-c", "--color", "-d", "--description", "-n", "--name")}, set()),
    "delete": (REPO_FLAGS, {"--yes"}),
    "clone": (REPO_FLAGS, {"-f", "--force"}),
}

RELEASE_COMMANDS = {
    "create": ({**REPO_FLAGS, **flags(
        "--discussion-category", "-n", "--notes", "-F", "--notes-file", "--notes-start-tag",
        "--target", "-t", "--title",
    )}, {"-d", "--draft", "--fail-on-no-commits", "--generate-notes", "--latest",
         "--notes-from-tag", "-p", "--prerelease", "--verify-tag"}),
    "new": ({**REPO_FLAGS, **flags(
        "--discussion-category", "-n", "--notes", "-F", "--notes-file", "--notes-start-tag",
        "--target", "-t", "--title",
    )}, {"-d", "--draft", "--fail-on-no-commits", "--generate-notes", "--latest",
         "--notes-from-tag", "-p", "--prerelease", "--verify-tag"}),
    "edit": ({**REPO_FLAGS, **flags(
        "--discussion-category", "-n", "--notes", "-F", "--notes-file", "--tag", "--target",
        "-t", "--title",
    )}, {"--draft", "--latest", "--prerelease", "--verify-tag"}),
    "delete": (REPO_FLAGS, {"--cleanup-tag", "-y", "--yes"}),
    "upload": (REPO_FLAGS, {"--clobber"}),
    "delete-asset": (REPO_FLAGS, {"-y", "--yes"}),
}

SECRET_COMMANDS = {
    "set": ({**REPO_FLAGS, **ORG_FLAGS, **flags(
        "-a", "--app", "-b", "--body", "-e", "--env", "-f", "--env-file", "-r", "--repos",
        "-v", "--visibility",
    )}, {"--no-repos-selected", "--no-store"}),
    "delete": ({**REPO_FLAGS, **ORG_FLAGS, **flags("-a", "--app", "-e", "--env")}, set()),
    "remove": ({**REPO_FLAGS, **ORG_FLAGS, **flags("-a", "--app", "-e", "--env")}, set()),
}

VARIABLE_COMMANDS = {
    "set": ({**REPO_FLAGS, **ORG_FLAGS, **flags(
        "-b", "--body", "-e", "--env", "-f", "--env-file", "-r", "--repos", "-v", "--visibility",
    )}, set()),
    "delete": ({**REPO_FLAGS, **ORG_FLAGS, **flags("-e", "--env")}, set()),
    "remove": ({**REPO_FLAGS, **ORG_FLAGS, **flags("-e", "--env")}, set()),
}

RULESET_COMMANDS = {
    "create": ({**REPO_FLAGS, **ORG_FLAGS}, set()),
    "update": ({**REPO_FLAGS, **ORG_FLAGS}, set()),
    "delete": ({**REPO_FLAGS, **ORG_FLAGS}, set()),
}

PROJECT_COMMON = flags("--format", "-q", "--jq", "-t", "--template")
PROJECT_COMMANDS = {
    "create": ({"--owner": "owner", **PROJECT_COMMON, "--title": "--title"}, set()),
    "copy": ({"--source-owner": "source-owner", "--target-owner": "target-owner",
              **PROJECT_COMMON, "--title": "--title"}, {"--drafts"}),
    "close": ({"--owner": "owner", **PROJECT_COMMON}, {"--undo"}),
    "delete": ({"--owner": "owner", **PROJECT_COMMON}, set()),
    "edit": ({"--owner": "owner", **PROJECT_COMMON, "-d": "-d", "--description": "--description",
              "--readme": "--readme", "--title": "--title", "--visibility": "--visibility"}, set()),
    "field-create": ({"--owner": "owner", **PROJECT_COMMON, "--data-type": "--data-type",
                      "--name": "--name", "--single-select-options": "--single-select-options"}, set()),
    "field-delete": ({"--owner": "owner", **PROJECT_COMMON, "--id": "--id"}, set()),
    "item-add": ({"--owner": "owner", **PROJECT_COMMON, "--url": "--url"}, set()),
    "item-archive": ({"--owner": "owner", **PROJECT_COMMON, "--id": "--id"}, {"--undo"}),
    "item-create": ({"--owner": "owner", **PROJECT_COMMON, "--body": "--body",
                     "--title": "--title"}, set()),
    "item-delete": ({"--owner": "owner", **PROJECT_COMMON, "--id": "--id"}, set()),
    "item-edit": ({"--owner": "owner", **PROJECT_COMMON, "--body": "--body", "--date": "--date",
                   "--field": "--field", "--field-id": "--field-id", "--id": "--id",
                   "--iteration-id": "--iteration-id", "--number": "--number",
                   "--project-id": "--project-id",
                   "--single-select-option-id": "--single-select-option-id",
                   "--text": "--text", "--title": "--title", "--url": "--url",
                   "--value": "--value"}, {"--clear"}),
    "link": ({"--owner": "owner", "-R": "repo", "--repo": "repo", "-T": "--team"}, set()),
    "unlink": ({"--owner": "owner", "-R": "repo", "--repo": "repo", "-T": "--team"}, set()),
    "mark-template": ({"--owner": "owner", **PROJECT_COMMON}, {"--undo"}),
}


def parse_command(args, commands, target_flags=TARGET_FLAGS):
    prefix, command, rest = split_target(args, target_flags)
    if command is None:
        return None, None, None
    if command not in commands:
        return command, None, None
    values, switches = commands[command]
    positional, parsed = options(prefix + rest, values, switches | {"--help"})
    return command, positional, parsed


def require_owned_repo(parsed, command):
    repos = parsed.get("repo", [])
    if len(repos) != 1:
        deny(f"gh {command} requires exactly one explicit -R/--repo repository")
    owned_repo(repos[0])


def validate_issue(args):
    command, positional, parsed = parse_command(args, ISSUE_COMMANDS)
    if parsed is None:
        return
    require_owned_repo(parsed, f"issue {command}")
    if command == "transfer":
        if len(positional) != 2:
            deny("issue transfer requires an issue and an owned destination repository")
        owned_repo(positional[1])


def validate_repo(args):
    command, positional, parsed = parse_command(args, REPO_COMMANDS)
    if parsed is None:
        return
    if command == "fork":
        orgs = parsed.get("org", [])
        if len(orgs) != 1:
            deny("repo fork requires exactly one explicit --org destination")
        owned_owner(orgs[0])
        return
    if command == "create":
        if len(positional) != 1:
            deny("repo create requires exactly one OWNER/NAME positional")
        owned_repo(positional[0])
        return
    if command == "rename":
        require_owned_repo(parsed, "repo rename")
        return
    targets = parsed.get("repo", []) + positional
    if len(targets) != 1:
        deny(f"repo {command} requires exactly one explicit owned repository")
    owned_repo(targets[0])


def validate_workflow(args):
    command, _positional, parsed = parse_command(args, WORKFLOW_COMMANDS)
    if parsed is None:
        return
    require_owned_repo(parsed, f"workflow {command}")


def validate_discussion(args):
    command, _positional, parsed = parse_command(args, DISCUSSION_COMMANDS)
    if parsed is None:
        return
    require_owned_repo(parsed, f"discussion {command}")


def validate_label(args):
    command, _positional, parsed = parse_command(args, LABEL_COMMANDS)
    if parsed is None:
        return
    require_owned_repo(parsed, f"label {command}")


def validate_release(args):
    command, _positional, parsed = parse_command(args, RELEASE_COMMANDS)
    if parsed is None:
        return
    require_owned_repo(parsed, f"release {command}")


def _validate_owned_targets(command, parsed, flag):
    repos = parsed.get("repo", [])
    orgs = parsed.get("org", [])
    if not repos and not orgs:
        deny(f"gh {command} requires an explicit owned {flag}")
    for repo in repos:
        owned_repo(repo)
    for org in orgs:
        owned_owner(org)


def validate_secret(args):
    command, _positional, parsed = parse_command(args, SECRET_COMMANDS, {"-R", "--repo", "-o", "--org"})
    if parsed is None:
        return
    _validate_owned_targets(f"secret {command}", parsed, "-R/--repo or -o/--org")


def validate_variable(args):
    command, _positional, parsed = parse_command(args, VARIABLE_COMMANDS, {"-R", "--repo", "-o", "--org"})
    if parsed is None:
        return
    _validate_owned_targets(f"variable {command}", parsed, "-R/--repo or -o/--org")


def validate_ruleset(args):
    command, _positional, parsed = parse_command(args, RULESET_COMMANDS, {"-R", "--repo", "-o", "--org"})
    if parsed is None:
        return
    _validate_owned_targets(f"ruleset {command}", parsed, "-R/--repo or --org")


def validate_project(args):
    command, _positional, parsed = parse_command(args, PROJECT_COMMANDS, {"--owner", "--target-owner"})
    if parsed is None:
        return
    if command == "copy":
        owners = parsed.get("target-owner", [])
        if len(owners) != 1:
            deny("project copy requires exactly one --target-owner")
        owned_owner(owners[0])
    else:
        owners = parsed.get("owner", [])
        if len(owners) != 1:
            deny(f"project {command} requires exactly one --owner")
        owned_owner(owners[0])
    if command in {"link", "unlink"}:
        for repo in parsed.get("repo", []):
            if "/" in repo:
                owned_repo(repo)


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
        if len(parts) < 3 or parts[0] != "repos":
            deny("mutations require a canonical repos/OWNER/REPO/... endpoint")
        owned_repo("/".join(parts[1:3]))
        if parts[-1].lower() == "pulls":
            if len(parts) != 4 or parts[0] != "repos" or parts[3] != "pulls":
                deny("PR creation requires the canonical repos/OWNER/REPO/pulls endpoint")
            owned_repo("/".join(parts[1:3]))
        elif parts[-1].lower() == "reverts":
            if len(parts) != 6 or parts[0] != "repos" or parts[3].lower() != "pulls":
                deny("PR revert requires the canonical repos/OWNER/REPO/pulls/N/reverts endpoint")
            owned_repo("/".join(parts[1:3]))


def main():
    backend = Path(__file__).resolve().with_name("real-gh").read_text().strip()
    if not os.path.isabs(backend) or not os.access(backend, os.X_OK) or Path(backend).resolve() == Path(__file__).resolve():
        deny("real gh backend is missing or recursive; reinstall the guard")
    args = sys.argv[1:]
    if not args or args[0] not in {
        "api", "auth", "browse", "cache", "codespace", "completion", "config", "discussion",
        "gist", "gpg-key", "help", "issue", "label", "org", "pr", "project", "release", "repo",
        "ruleset", "run", "search", "secret", "ssh-key", "status", "variable", "workflow",
        "version", "--version", "--help",
    }:
        deny("only builtin commands are supported; aliases and extensions are disabled")
    aliases = subprocess.run([backend, "alias", "list"], capture_output=True, text=True)
    if aliases.returncode:
        deny("cannot check configured gh aliases")
    if any(line.split(":", 1)[0].strip() == args[0] for line in aliases.stdout.splitlines()):
        deny("configured alias execution is disabled; use the builtin command explicitly")
    if os.environ.get("GH_HOST", "github.com").lower() != "github.com":
        deny("only github.com is supported")
    validators = {
        "issue": validate_issue,
        "repo": validate_repo,
        "workflow": validate_workflow,
        "discussion": validate_discussion,
        "label": validate_label,
        "release": validate_release,
        "secret": validate_secret,
        "variable": validate_variable,
        "ruleset": validate_ruleset,
        "project": validate_project,
    }
    if args[0] == "pr":
        validate_pr(args[1:])
    elif args[0] == "api":
        validate_api(args[1:])
    elif args[0] in validators:
        validators[args[0]](args[1:])
    os.execv(backend, [backend, *args])


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"gh cli wrapper: {error}", file=sys.stderr)
        sys.exit(1)
