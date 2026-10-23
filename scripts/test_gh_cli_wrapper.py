import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if sys.argv[1:] == ["alias", "list"]:
    print(os.environ.get("TEST_ALIASES", ""), end="")
    sys.exit(int(os.environ.get("TEST_ALIAS_STATUS", "0")))
log_path = Path(os.environ["TEST_LOG"])
index = len(log_path.read_text().splitlines()) if log_path.exists() else 0
with log_path.open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
script = json.loads(os.environ.get("TEST_SCRIPT", "null"))
if script is not None:
    spec = script.get(str(index), script.get("tail"))
    if spec is not None:
        sys.stdout.write(spec.get("stdout", ""))
        sys.stderr.write(spec.get("stderr", ""))
        sys.exit(int(spec.get("status", 0)))
print(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read()}))
print("backend stderr", file=sys.stderr)
sys.exit(int(os.environ.get("TEST_STATUS", "0")))
'''

SIG403 = {"stdout": "", "stderr": "gh: HTTP 403 (https://api.github.com/graphql)\n", "status": 1}


def prepare_backend(directory):
    backend = directory / "bin" / "gh"
    backend.parent.mkdir(parents=True, exist_ok=True)
    backend.write_text(FAKE)
    backend.chmod(0o755)
    return backend


def verify_installed():
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ, TEST_LOG=str(Path(directory) / "log"), TEST_STATUS="23")
        argv = ["pr", "create", "-R", "BaClUc-AgEnT/ecamp3", "--body-file", "-"]
        result = subprocess.run(["bash", "-c", 'exec gh "$@"', "test", *argv],
                                cwd=directory, env=env, input="body from stdin",
                                capture_output=True, text=True)
        assert result.returncode == 23, result.stderr
        assert json.loads(result.stdout) == {"argv": argv, "stdin": "body from stdin"}
        assert result.stderr == "backend stderr\n"
        log = Path(env["TEST_LOG"])
        log.unlink()
        result = subprocess.run(["bash", "-c", 'gh pr create -R ecamp/ecamp3 --base devel --head bacluc-agent:issue-221-move-doctrine-validate-to-required-ci'],
                                cwd=directory, env=env, capture_output=True, text=True)
        assert result.returncode != 0
        assert "gh cli wrapper:" in result.stderr
        assert not log.exists()
        for denied in ("gh pr create -R ecamp/ecamp3 --repo ecamp/ecamp3",
                       "gh pr create --repo ecamp/ecamp3 --base devel --head bacluc-agent:issue-221-move-doctrine-validate-to-required-ci"):
            result = subprocess.run(["bash", "-c", denied], cwd=directory, env=env,
                                    capture_output=True, text=True)
            assert result.returncode != 0, denied
            assert "gh cli wrapper:" in result.stderr, denied
            assert not log.exists(), denied
    print("Installed composite wrapper: forwarding and historical denial passed")


class CliWrapperTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.backend = prepare_backend(self.directory)
        self.env = dict(os.environ, RUNNER_TEMP=str(self.directory),
                        GITHUB_PATH=str(self.directory / "path"),
                        PATH=f"{self.backend.parent}:{os.environ['PATH']}",
                        TEST_LOG=str(self.directory / "log"), GH_HOST="github.com")
        result = subprocess.run(["bash", str(ROOT / "scripts/install-gh-cli-wrapper.sh")],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.env["PATH"] = f"{(self.directory / 'path').read_text().strip()}:{self.env['PATH']}"

    def call(self, argv, allowed=True, **env):
        log = Path(self.env["TEST_LOG"])
        log.unlink(missing_ok=True)
        result = subprocess.run(["bash", "-c", 'exec gh "$@"', "test", *argv],
                                cwd="/tmp", env=dict(self.env, **env), input="payload\n",
                                capture_output=True, text=True)
        if allowed:
            self.assertEqual(result.returncode, int(env.get("TEST_STATUS", "0")), result.stderr)
            self.assertEqual(json.loads(result.stdout), {"argv": argv, "stdin": "payload\n"})
            self.assertEqual(result.stderr, "backend stderr\n")
            self.assertEqual(log.read_text().splitlines(), [json.dumps(argv)])
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("gh cli wrapper:", result.stderr)
            self.assertFalse(log.exists(), result.stdout)

    def script_call(self, argv, script, **env):
        log = Path(self.env["TEST_LOG"])
        log.unlink(missing_ok=True)
        result = subprocess.run(["bash", "-c", 'exec gh "$@"', "test", *argv],
                                cwd="/tmp", env=dict(self.env, TEST_SCRIPT=json.dumps(script), **env),
                                input="payload\n", capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    def test_creation(self):
        self.call(["pr", "create", "-R", "ecamp/ecamp3", "--base", "devel", "--head",
                   "bacluc-agent:issue-221-move-doctrine-validate-to-required-ci"], False)
        for command in ("create", "new"):
            for repo in ("BacLuc/r", "BaClUc-AgEnT/r", "github.com/BacLuc/r", "https://github.com/BacLuc/r"):
                for flags in (["-R", repo], ["-R" + repo], ["-R=" + repo], ["--repo", repo], ["--repo=" + repo]):
                    self.call(["pr", command, *flags, "--title", "--repo=outsider/r", "--body", "-Rbad/r"])
            for flags in ([], ["-R", "outsider/r"], ["-R", "BacLuc-evil/r"],
                          ["-R", "evil.com/BacLuc/r"], ["-R", "BacLuc/r", "--repo", "BacLuc/r"],
                          ["-R", "ecamp/ecamp3", "--repo", "ecamp/ecamp3"], ["--repo", "ecamp/ecamp3"],
                          ["--body", "--repo=BacLuc/r"], ["--title=-RBacLuc/r"],
                          ["-R", "BacLuc/r", "--unknown"], ["-R"], ["--", "-R", "BacLuc/r"]):
                self.call(["pr", command, *flags], False)
        self.call(["pr", "-RBacLuc/r", "create", "--body=-Rbad/r"])
        self.call(["pr", "create", "-RBacLuc/r"], False, GH_HOST="evil.com")

    def test_api(self):
        for endpoint in ("repos/ecamp/ecamp3/pulls", "/repos/ecamp/ecamp3/pulls", "https://api.github.com/repos/ecamp/ecamp3/pulls"):
            for flags in (["-X", "POST"], ["--method=POST"], ["-XPOST"], ["-f", "title=test"], ["-Ftitle=test"], ["--input", "-"]):
                self.call(["api", endpoint, *flags], False)
                self.call(["api", endpoint.replace("ecamp/ecamp3", "BaClUc-AgEnT/r"), *flags])
            self.call(["api", endpoint])
            self.call(["api", endpoint, "-XGET", "-f", "q=hello"])
        for endpoint in ("graphql", "/graphql", "https://api.github.com/graphql", "repos/{owner}/{repo}/pulls",
                         "repos/ecamp/ecamp3/pulls/", "repos/BacLuc/r/../r/pulls", "repos/BacLuc/r/%70ulls",
                         "repos/BacLuc/r//pulls", "repos/BacLuc/r/pulls?x=1", "https://evil.com/repos/BacLuc/r/pulls",
                         "https://api.github.com//repos/BacLuc/r/pulls", "repositories/123/pulls",
                         "api/v3/repos/BacLuc/r/pulls"):
            self.call(["api", endpoint, "--input=-"], False)
        for flags in (["--hostname", "evil.com"], ["--hostname=github.com", "--hostname=github.com"],
                      ["-XPOST", "-XGET"], ["-HHost:evil.com"], ["-HX-HTTP-Method-Override:POST"],
                      ["--unknown"], ["repos/BacLuc/r/pulls"]):
            self.call(["api", "repos/ecamp/ecamp3/pulls", *flags], False)
        self.call(["api", "repos/BacLuc/r/pulls/", "-X", "POST", "-f", "title=test"])
        self.call(["api", "repos/BacLuc/r/contents/", "--jq", ".[].name"])
        self.call(["api", "graphql", "-XGET"], False)
        self.call(["api", "repos/BacLuc/r/pulls", "-f", "body=--hostname=evil.com", "--hostname=github.com"])

    def test_revert(self):
        for argv in (
            ["pr", "revert", "123", "-R", "ecamp/ecamp3", "--title", "Revert change", "--body", "Revert change"],
            ["pr", "-R", "ecamp/ecamp3", "revert", "123"],
            ["pr", "--repo=ecamp/ecamp3", "revert", "123"],
            ["pr", "revert", "https://github.com/ecamp/ecamp3/pull/123"],
            ["pr", "revert", "123"],
            ["pr", "revert", "123", "-R", "BacLuc/r", "-R", "ecamp/ecamp3"],
            ["pr", "revert", "-R", "BacLuc/r"],
            ["pr", "revert", "--help"],
            ["pr", "revert", "123", "-R", "BacLuc/r", "--unknown"],
            ["api", "repos/ecamp/ecamp3/pulls/123/reverts", "-XPOST"],
            ["api", "repos/ecamp/ecamp3/pulls/123/reverts", "-f", "title=test"],
        ):
            self.call(argv, False)
        for argv in (
            ["pr", "revert", "123", "-R", "BaClUc-AgEnT/r", "--title", "Revert change", "--body", "Revert change"],
            ["pr", "revert", "https://github.com/BacLuc/r/pull/123"],
            ["pr", "-R", "BacLuc/r", "revert", "123"],
            ["api", "repos/BacLuc/r/pulls/123/reverts", "-XPOST"],
            ["api", "repos/ecamp/ecamp3/pulls/123/reverts", "-XGET"],
        ):
            self.call(argv)

    def test_other_calls_and_aliases(self):
        for argv in (["issue", "comment", "234", "-R", "outsider/r", "--body", "pr create"],
                     ["pr", "view", "10800", "-R", "ecamp/ecamp3"], ["search", "issues", "test"],
                     ["repo", "fork", "ecamp/ecamp3", "--org", "bacluc-agent"],
                     ["workflow", "run", "ci.yml", "--ref", "issue-234"],
                     ["api", "repos/ecamp/ecamp3/issues/1/comments", "-fbody=test"],
                     ["api", "repos/{owner}/{repo}/issues/comments/1", "-XPATCH", "-fbody=test"],
                     ["api", "repos/ecamp/ecamp3/forks", "-XPOST"],
                     ["api", "search/issues", "-XGET", "-fq=repo:ecamp/ecamp3"],
                     ["api", "repos/ecamp/ecamp3/actions/workflows/ci.yml/dispatches", "-fref=main"]):
            self.call(argv, TEST_STATUS="17")
        for argv in (["alias", "set", "p", "pr create"], ["extension", "exec", "p"], ["extensions", "exec", "p"],
                     ["custom"], ["--repo=BacLuc/r", "pr", "create"]):
            self.call(argv, False)
        self.call(["pr", "view"], False, TEST_ALIASES="pr: !echo bypass\n")
        self.call(["pr", "view"], False, TEST_ALIAS_STATUS="1")
        self.call(["pr", "view"], TEST_ALIASES="co: pr checkout\n")

    def test_fallback_passthrough(self):
        shapes = (
            ["issue", "view", "1", "-R", "BacLuc/r"],
            ["issue", "view", "1", "-R", "BacLuc/r", "--json", "title", "--web"],
            ["issue", "view", "1", "-R", "BacLuc/r", "--json", "title", "--json", "body"],
            ["issue", "view", "1", "-R", "BacLuc/r", "-R", "BacLuc/r", "--json", "title"],
            ["issue", "view", "1", "-R"],
            ["issue", "view", "1", "-R", "--json", "title"],
            ["pr", "list", "-R", "BacLuc/r", "--search", "x", "--json", "title"],
            ["pr", "view", "feature/x", "-R", "BacLuc/r", "--json", "title"],
            ["issue", "view", "https://github.com/BacLuc/r/issues/1", "-R", "BacLuc/r", "--json", "title"],
            ["repo", "view", "--json", "nameWithOwner"],
            ["repo", "list", "--json", "nameWithOwner"],
            ["pr", "list", "-R", "BacLuc/r", "--json", "title", "--limit", "abc"],
            ["pr", "list", "-R", "BacLuc/r", "--json", "title", "--state", "merged-and-open"],
        )
        for shape in shapes:
            result, calls = self.script_call(shape, {"tail": SIG403})
            self.assertEqual(result.returncode, 1, shape)
            self.assertEqual(result.stdout, "", shape)
            self.assertEqual(result.stderr, "gh: HTTP 403 (https://api.github.com/graphql)\n", shape)
            self.assertEqual(calls, [shape], shape)
        result, calls = self.script_call(["repo", "view", "ecamp/ecamp3", "--json", "url"],
                                         {"tail": {"stdout": "", "stderr": "boom\n", "status": 17}})
        self.assertEqual(result.returncode, 17)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "boom\n")
        self.assertEqual(calls, [["repo", "view", "ecamp/ecamp3", "--json", "url"]])

    def test_issue_view_fallback(self):
        rest = {"number": 123, "title": "T", "body": "B", "state": "open",
                "created_at": "2026-10-22T10:00:00Z", "updated_at": "2026-10-22T11:00:00Z",
                "html_url": "https://github.com/BacLuc/r-2/issues/123"}
        script = {"0": SIG403, "1": {"stdout": json.dumps(rest)}}
        result, calls = self.script_call(
            ["issue", "view", "123", "--json", "title", "-R", "GitHub.com/BacLuc/r-2", "--jq", ".title"], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "T\n")
        self.assertEqual(calls, [["issue", "view", "123", "--json", "title", "-R", "GitHub.com/BacLuc/r-2",
                                  "--jq", ".title"],
                                 ["api", "repos/BacLuc/r-2/issues/123"]])
        result, calls = self.script_call(
            ["issue", "view", "123", "--json", "number,title,url,state,createdAt,updatedAt,body",
             "-R", "GitHub.com/BacLuc/r-2"], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, json.dumps(
            {"number": 123, "title": "T", "url": "https://github.com/BacLuc/r-2/issues/123", "state": "OPEN",
             "createdAt": "2026-10-22T10:00:00Z", "updatedAt": "2026-10-22T11:00:00Z", "body": "B"},
            indent=2) + "\n")

    def test_issue_view_comments_fallback(self):
        bot = {"user": {"login": "bacluc-agent"}, "body": "bot", "created_at": "2026-03-01T00:00:00Z"}
        page1 = [bot] + [{"user": {"login": f"user{i}"}, "body": f"c{i}",
                          "created_at": f"2026-01-01T01:{i:02d}:00Z"} for i in range(99)]
        page2 = [{"user": {"login": "user97"}, "body": "old", "created_at": "2026-01-03T00:00:00Z"},
                 {"user": {"login": "alice"}, "body": "newest", "created_at": "2026-02-01T00:00:00Z"},
                 {"user": {"login": "bob"}, "body": "x", "created_at": "2026-01-31T00:00:00Z"}]
        script = {"0": SIG403,
                  "1": {"stdout": json.dumps({"number": 7, "title": "t", "body": "b", "state": "open",
                                              "created_at": "2026-01-01T00:00:00Z",
                                              "updated_at": "2026-01-02T00:00:00Z",
                                              "html_url": "https://github.com/BacLuc/r/issues/7"})},
                  "2": {"stdout": json.dumps(page1)},
                  "3": {"stdout": json.dumps(page2)}}
        result, calls = self.script_call(
            ["issue", "view", "7", "-R", "BacLuc/r", "--json", "comments", "--jq",
             '[.comments[] | select(.author.login != "bacluc-agent")] | max_by(.createdAt) | .createdAt // "none"'],
            script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1:], [["api", "repos/BacLuc/r/issues/7"],
                                     ["api", "repos/BacLuc/r/issues/7/comments?per_page=100&page=1"],
                                     ["api", "repos/BacLuc/r/issues/7/comments?per_page=100&page=2"]])
        self.assertEqual(result.stdout, "2026-02-01T00:00:00Z\n")
        result, calls = self.script_call(["issue", "view", "7", "-R", "BacLuc/r", "--json", "body",
                                          "--jq", ".body"], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1], ["api", "repos/BacLuc/r/issues/7"])
        self.assertEqual(result.stdout, "b\n")

    def test_pr_view_fallback(self):
        comments = [{"user": {"login": "alice"}, "body": "c", "created_at": "2026-02-01T00:00:00Z"},
                    {"user": {"login": "bacluc-agent"}, "body": "b", "created_at": "2026-03-01T00:00:00Z"}]
        reviews = [{"user": {"login": "bacluc-agent"}, "submitted_at": "2026-04-01T00:00:00Z"},
                   {"user": {"login": "bob"}, "submitted_at": "2026-01-15T00:00:00Z"}]
        pull = {"number": 123, "title": "T", "body": "B", "state": "open",
                "created_at": "2026-10-22T10:00:00Z", "updated_at": "2026-10-22T11:00:00Z",
                "head": {"ref": "feature/x"}, "merged_at": None,
                "html_url": "https://github.com/BacLuc/r/pull/123"}
        script = {"0": SIG403, "1": {"stdout": json.dumps(pull)},
                  "2": {"stdout": json.dumps(comments)}, "3": {"stdout": json.dumps(reviews)}}
        result, calls = self.script_call(
            ["pr", "view", "123", "-R", "BacLuc/r", "--json", "comments,reviews", "--jq",
             '[[.comments[] | select(.author.login != "bacluc-agent") | .createdAt], '
             '[.reviews[] | select(.author.login != "bacluc-agent") | .submittedAt]] | add | max // "none"'],
            script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1:], [["api", "repos/BacLuc/r/pulls/123"],
                                     ["api", "repos/BacLuc/r/issues/123/comments?per_page=100&page=1"],
                                     ["api", "repos/BacLuc/r/pulls/123/reviews?per_page=100&page=1"]])
        self.assertEqual(result.stdout, "2026-02-01T00:00:00Z\n")
        only_bot = {"0": SIG403, "1": {"stdout": json.dumps(pull)},
                    "2": {"stdout": json.dumps([{"user": {"login": "bacluc-agent"}, "body": "b",
                                                 "created_at": "2026-03-01T00:00:00Z"}])},
                    "3": {"stdout": json.dumps([{"user": {"login": "bacluc-agent"},
                                                 "submitted_at": "2026-04-01T00:00:00Z"}])}}
        result, calls = self.script_call(
            ["pr", "view", "123", "-R", "BacLuc/r", "--json", "comments,reviews", "--jq",
             '[[.comments[] | select(.author.login != "bacluc-agent") | .createdAt], '
             '[.reviews[] | select(.author.login != "bacluc-agent") | .submittedAt]] | add | max // "none"'],
            only_bot)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "none\n")
        raw = {"0": SIG403, "1": {"stdout": json.dumps({"number": 123, "title": "T", "state": "closed",
                                                       "merged_at": "2026-01-02T00:00:00Z",
                                                       "head": {"ref": "feature/x"},
                                                       "created_at": "2026-01-01T00:00:00Z",
                                                       "updated_at": "2026-01-03T00:00:00Z",
                                                       "html_url": "https://github.com/BacLuc/r/pull/123"})}}
        result, calls = self.script_call(
            ["pr", "view", "123", "-R", "BacLuc/r", "--json", "number,title,headRefName,state,createdAt,updatedAt,url"],
            raw)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, json.dumps(
            {"number": 123, "title": "T", "headRefName": "feature/x", "state": "MERGED",
             "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-03T00:00:00Z",
             "url": "https://github.com/BacLuc/r/pull/123"}, indent=2) + "\n")

    def test_pr_list_fallback(self):
        def pr(number, state, merged=None):
            hour = number % 24
            return {"number": number, "title": f"t{number}", "state": state, "merged_at": merged,
                    "head": {"ref": f"branch-{number}"},
                    "created_at": f"2026-01-01T{hour:02d}:00:00Z",
                    "updated_at": f"2026-02-01T{hour:02d}:00:00Z",
                    "html_url": f"https://github.com/BacLuc/r/pull/{number}"}
        page1 = [pr(number, "open") for number in range(1, 101)]
        page1[56]["head"] = {"ref": "myb1"}
        page2 = [pr(456, "closed", merged="2026-01-05T00:00:00Z"), pr(457, "closed"), pr(458, "open")]
        script = {"0": SIG403, "1": {"stdout": json.dumps(page1)}, "2": {"stdout": json.dumps(page2)}}
        argv = ["pr", "list", "-R", "BacLuc/r", "--state", "all", "--limit", "103", "--json",
                "number,createdAt,updatedAt,headRefName,state,title",
                "--jq", '.[] | "\\(.headRefName)|#\\(.number)|\\(.createdAt)|\\(.updatedAt)|\\(.state)|\\(.title)"']
        result, calls = self.script_call(argv, script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1:], [["api", "repos/BacLuc/r/pulls?state=all&per_page=100&page=1"],
                                     ["api", "repos/BacLuc/r/pulls?state=all&per_page=100&page=2"]])
        rows = result.stdout.splitlines()
        self.assertEqual(len(rows), 103)
        self.assertEqual(rows[100], "branch-456|#456|2026-01-01T00:00:00Z|2026-02-01T00:00:00Z|MERGED|t456")
        self.assertEqual(rows[101], "branch-457|#457|2026-01-01T01:00:00Z|2026-02-01T01:00:00Z|CLOSED|t457")
        self.assertEqual(rows[102], "branch-458|#458|2026-01-01T02:00:00Z|2026-02-01T02:00:00Z|OPEN|t458")
        result, calls = self.script_call(["pr", "list", "-R", "BacLuc/r", "--json", "title", "--limit", "5"], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1], ["api", "repos/BacLuc/r/pulls?state=open&per_page=100&page=1"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(result.stdout), [{"title": f"t{number}"} for number in range(1, 6)])
        for head in ("myb1", "owner:myb1"):
            result, calls = self.script_call(
                ["pr", "list", "-R", "BacLuc/r", "--head", head, "--json", "url", "--jq", ".[0].url"], script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[1], ["api", "repos/BacLuc/r/pulls?state=open&per_page=100&page=1"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(result.stdout, "https://github.com/BacLuc/r/pull/57\n")
            result, calls = self.script_call(
                ["pr", "list", "-R", "BacLuc/r", "--state", "all", "--head", head, "--limit", "100",
                 "--json", "url", "--jq", ".[0].url"], script)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls[1:], [["api", "repos/BacLuc/r/pulls?state=all&per_page=100&page=1"]])
            self.assertEqual(len(calls), 2)
            self.assertEqual(result.stdout, "https://github.com/BacLuc/r/pull/57\n")

    def test_repo_view_fallback(self):
        rest = {"full_name": "ecamp/ecamp3", "description": "d",
                "html_url": "https://github.com/ecamp/ecamp3", "name": "ecamp3"}
        script = {"0": SIG403, "1": {"stdout": json.dumps(rest)}}
        result, calls = self.script_call(
            ["repo", "view", "https://github.com/ecamp/ecamp3", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1], ["api", "repos/ecamp/ecamp3"])
        self.assertEqual(result.stdout, "ecamp/ecamp3\n")
        result, calls = self.script_call(
            ["repo", "view", "https://github.com/ecamp/ecamp3", "--json", "nameWithOwner,url,description"], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, json.dumps(
            {"nameWithOwner": "ecamp/ecamp3", "url": "https://github.com/ecamp/ecamp3", "description": "d"},
            indent=2) + "\n")

    def test_repo_list_fallback(self):
        page1 = [{"full_name": f"bacluc-agent/r{i}"} for i in range(100)]
        page2 = [{"full_name": "bacluc-agent/z1"}, {"full_name": "bacluc-agent/z2"}]
        script = {"0": SIG403, "1": {"stdout": json.dumps(page1)}, "2": {"stdout": json.dumps(page2)}}
        result, calls = self.script_call(
            ["repo", "list", "bacluc-agent", "--limit", "102", "--json", "nameWithOwner",
             "--jq", ".[].nameWithOwner"], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1:], [["api", "users/bacluc-agent/repos?per_page=100&page=1"],
                                     ["api", "users/bacluc-agent/repos?per_page=100&page=2"]])
        self.assertEqual(result.stdout, "\n".join(f"bacluc-agent/r{i}" for i in range(100))
                         + "\nbacluc-agent/z1\nbacluc-agent/z2\n")
        result, calls = self.script_call(["repo", "list", "bacluc-agent", "--json", "nameWithOwner",
                                          "--jq", ".[].nameWithOwner"], script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[1], ["api", "users/bacluc-agent/repos?per_page=100&page=1"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.stdout, "\n".join(f"bacluc-agent/r{i}" for i in range(30)) + "\n")

    def test_unmapped_field_deny_after_403(self):
        argv = ["issue", "view", "1", "-R", "BacLuc/r", "--json", "author,title"]
        result, calls = self.script_call(argv, {"tail": SIG403})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("gh cli wrapper:", result.stderr)
        self.assertIn("'author'", result.stderr)
        self.assertIn("gh api repos/BacLuc/r/issues/1", result.stderr)
        self.assertEqual(calls, [argv])

    def test_rest_failure_surfacing(self):
        script = {"0": SIG403, "1": {"stdout": "", "stderr": "gh: rate limit exceeded\n", "status": 1}}
        result, calls = self.script_call(["issue", "view", "1", "-R", "BacLuc/r", "--json", "title"], script)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("gh: rate limit exceeded", result.stderr)
        self.assertEqual(result.stderr.count("gh cli wrapper:"), 1)
        self.assertEqual(calls[1], ["api", "repos/BacLuc/r/issues/1"])
        self.assertEqual(len(calls), 2)

    def test_subsequent_step_probe(self):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--verify-installed"],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_installer_recursion_and_missing_backend(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/install-gh-cli-wrapper.sh")],
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gh cli wrapper:", result.stderr)
        self.backend.unlink()
        self.call(["pr", "view"], False)
        empty = self.directory / "empty"
        empty.mkdir()
        result = subprocess.run(["/bin/bash", str(ROOT / "scripts/install-gh-cli-wrapper.sh")],
                                env=dict(self.env, PATH=str(empty)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gh cli wrapper:", result.stderr)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--prepare-backend":
        print(prepare_backend(Path(sys.argv[2])).parent)
    elif sys.argv[1:] == ["--verify-installed"]:
        verify_installed()
    else:
        unittest.main()
