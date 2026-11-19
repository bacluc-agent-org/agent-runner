#!/usr/bin/env python3
import os
from pathlib import Path
import re
import subprocess
import sys


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
        validate_graphql(parsed)
        return
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


def validate_graphql(parsed):
    fields = parsed.get("body", [])
    queries = [field.partition("=")[2] for field in fields if field.startswith("query=")]
    form_fields = {
        "owner", "number", "projectId", "itemId", "fieldId", "afterId", "singleSelectOptionId",
    }
    if len(queries) != 1 or any(
        field.partition("=")[0] not in {"query", *form_fields} for field in fields
    ):
        deny("GraphQL requires a project-v2 query")
    document = GraphQLParser(queries[0]).parse()
    variables = {field.partition("=")[0] for field in fields if field.partition("=")[0] != "query"}
    if not variables <= form_fields:
        deny("GraphQL variables are not permitted")
    for variable, path in document["uses"]:
        if variable not in variables or (variable, path) not in {
            ("owner", ("organization", "login")),
            ("number", ("projectV2", "number")),
            ("projectId", ("input", "projectId")),
            ("itemId", ("input", "itemId")),
            ("fieldId", ("input", "fieldId")),
            ("afterId", ("input", "afterId")),
            ("singleSelectOptionId", ("value", "singleSelectOptionId")),
        }:
            deny("GraphQL variable is used outside its permitted project-v2 argument")


class GraphQLParser:
    names = {
        "ProjectV2", "ProjectV2FieldConfiguration", "ProjectV2Item", "ProjectV2SingleSelectField",
        "organization", "projectV2", "projectV2Items", "updateProjectV2ItemFieldValue",
        "updateProjectV2ItemPosition", "query", "mutation", "node", "id", "number", "after",
        "afterId", "first", "last", "field", "fields", "input", "items", "nodes", "pageInfo",
        "hasNextPage", "endCursor", "position", "fieldId", "singleSelectOptionId", "projectId",
        "itemId", "organizationId", "owner", "login", "cursor", "value", "clientMutationId",
        "projectV2Item", "null", "true", "false", "Int", "String", "ID", "name", "options",
        "content", "number", "title", "url", "type", "body", "state", "__typename",
    }

    def __init__(self, source):
        self.tokens = self.tokenize(source)
        self.index = 0
        self.uses = []

    @staticmethod
    def tokenize(source):
        tokens = []
        index = 0
        while index < len(source):
            if source[index].isspace() or source[index] == ",":
                index += 1
            elif source[index] == "#":
                index = source.find("\n", index)
                if index < 0:
                    break
            elif source[index] == '"':
                end = index + 1
                while end < len(source):
                    if source[end] == "\\":
                        end += 2
                    elif source[end] == '"':
                        end += 1
                        break
                    else:
                        end += 1
                if end > len(source) or source[end - 1] != '"':
                    deny("malformed GraphQL string")
                tokens.append(("string", source[index:end]))
                index = end
            elif source[index] in "!$():={}[]":
                tokens.append((source[index], source[index]))
                index += 1
            else:
                match = re.match(r"[A-Za-z_][A-Za-z0-9_]*|-?[0-9]+(?:\.[0-9]+)?", source[index:])
                if not match:
                    deny("malformed GraphQL query")
                value = match.group(0)
                tokens.append(("name", value))
                index += len(value)
        return tokens

    def take(self, kind=None, value=None):
        if self.index >= len(self.tokens):
            deny("malformed GraphQL query")
        token = self.tokens[self.index]
        if (kind and token[0] != kind) or (value and token[1] != value):
            deny("malformed GraphQL query")
        self.index += 1
        return token[1]

    def parse(self):
        operation = self.take("name")
        if operation not in {"query", "mutation"}:
            deny("GraphQL requires an explicit project-v2 operation")
        if self.index < len(self.tokens) and self.tokens[self.index][1] == "(":
            self.parse_variables()
        roots = self.selection_set(())
        if self.index != len(self.tokens) or len(roots) != 1:
            deny("GraphQL requires one project-v2 root selection")
        root = roots[0]
        if operation == "query" and root["name"] not in {"organization", "node"}:
            deny("only project-v2 query roots are supported")
        if operation == "mutation" and root["name"] not in {
            "updateProjectV2ItemFieldValue", "updateProjectV2ItemPosition",
        }:
            deny("only project-v2 mutation roots are supported")
        self.validate_root(operation, root)
        return {"uses": self.uses}

    def validate_root(self, operation, root):
        if operation == "query":
            if set(root["arguments"]) != ({"login"} if root["name"] == "organization" else {"id"}):
                deny("malformed project-v2 query root")
            children = root["children"]
            if root["name"] == "organization":
                if len(children) != 1 or children[0]["name"] != "projectV2":
                    deny("project-v2 query must select organization.projectV2")
                if set(children[0]["arguments"]) != {"number"}:
                    deny("malformed project-v2 selection")
                children = children[0]["children"]
            elif any(child["name"] not in {
                "id", "number", "title", "url", "fields", "items", "pageInfo", "nodes",
                "hasNextPage", "endCursor", "name", "options", "type",
            } for child in children):
                deny("node is not a supported project-v2 shape")
            if not children or any(child["name"] not in {
                "id", "number", "title", "url", "fields", "items", "pageInfo", "nodes",
                "hasNextPage", "endCursor", "name", "options", "content", "type", "body", "state",
            } for child in children):
                deny("unrelated project selection")
            return
        if set(root["arguments"]) != {"input"} or len(root["children"]) != 1:
            deny("malformed project-v2 mutation root")
        input_fields = root["arguments"]["input"]
        if root["name"] == "updateProjectV2ItemPosition":
            required = {"projectId", "itemId", "afterId"}
        else:
            required = {"projectId", "itemId", "fieldId", "value"}
        if not isinstance(input_fields, dict) or set(input_fields) != required or root["children"][0]["name"] != "projectV2Item":
            deny("malformed project-v2 mutation input")
        if root["name"] == "updateProjectV2ItemFieldValue":
            if not isinstance(input_fields["value"], dict) or set(input_fields["value"]) != {"singleSelectOptionId"}:
                deny("malformed project-v2 field value")
        if [child["name"] for child in root["children"][0]["children"]] != ["id"]:
            deny("malformed project-v2 mutation selection")

    def parse_variables(self):
        self.take(value="(")
        while self.index < len(self.tokens) and self.tokens[self.index][1] != ")":
            self.take(value="$")
            self.take("name")
            self.take(value=":")
            self.take("name")
            if self.index < len(self.tokens) and self.tokens[self.index][1] == "!":
                self.take(value="!")
        self.take(value=")")

    def selection_set(self, path):
        self.take(value="{")
        selections = []
        while self.index < len(self.tokens) and self.tokens[self.index][1] != "}":
            name = self.take("name")
            if self.index < len(self.tokens) and self.tokens[self.index][1] == ":":
                deny("GraphQL aliases are not permitted")
            arguments = self.arguments(path + (name,)) if self.index < len(self.tokens) and self.tokens[self.index][1] == "(" else {}
            children = self.selection_set(path + (name,)) if self.index < len(self.tokens) and self.tokens[self.index][1] == "{" else []
            if name not in self.names:
                deny("only project-v2 GraphQL selections are supported")
            selections.append({"name": name, "arguments": arguments, "children": children})
        self.take(value="}")
        if not selections:
            deny("malformed GraphQL selection")
        return selections

    def arguments(self, path):
        result = {}
        self.take(value="(")
        while self.index < len(self.tokens) and self.tokens[self.index][1] != ")":
            name = self.take("name")
            self.take(value=":")
            result[name] = self.value(path + (name,))
        self.take(value=")")
        return result

    def value(self, path):
        if self.index >= len(self.tokens):
            deny("malformed GraphQL value")
        if self.tokens[self.index][1] == "$":
            self.take(value="$")
            variable = self.take("name")
            self.uses.append((variable, path[-2:]))
            return variable
        if self.tokens[self.index][1] == "{":
            self.take(value="{")
            result = {}
            while self.index < len(self.tokens) and self.tokens[self.index][1] != "}":
                name = self.take("name")
                self.take(value=":")
                result[name] = self.value(path + (name,))
            self.take(value="}")
            return result
        token = self.tokens[self.index]
        if token[0] not in {"name", "string"}:
            deny("malformed GraphQL value")
        self.index += 1
        return token[1]


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
    os.execv(backend, [backend, *args])


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(f"gh cli wrapper: {error}", file=sys.stderr)
        sys.exit(1)
