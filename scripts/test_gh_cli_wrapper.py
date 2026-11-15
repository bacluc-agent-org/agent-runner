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
with Path(os.environ["TEST_LOG"]).open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read()}))
print("backend stderr", file=sys.stderr)
sys.exit(int(os.environ.get("TEST_STATUS", "0")))
'''


def prepare_backend(directory):
    backend = directory / "bin" / "gh"
    backend.parent.mkdir(parents=True, exist_ok=True)
    backend.write_text(FAKE)
    backend.chmod(0o755)
    return backend


def verify_installed():
    with tempfile.TemporaryDirectory() as directory:
        env = dict(os.environ, TEST_LOG=str(Path(directory) / "log"), TEST_STATUS="23")
        log = Path(env["TEST_LOG"])
        for repo in ("BaClUc-AgEnT/ecamp3", "bacluc-agent-org/r"):
            argv = ["pr", "create", "-R", repo, "--body-file", "-"]
            result = subprocess.run(["bash", "-c", 'exec gh "$@"', "test", *argv],
                                    cwd=directory, env=env, input="body from stdin",
                                    capture_output=True, text=True)
            assert result.returncode == 23, result.stderr
            assert json.loads(result.stdout) == {"argv": argv, "stdin": "body from stdin"}
            assert result.stderr == "backend stderr\n"
            log.unlink()
        result = subprocess.run(["bash", "-c", 'gh pr create -R ecamp/ecamp3 --base devel --head bacluc-agent:issue-221-move-doctrine-validate-to-required-ci'],
                                cwd=directory, env=env, capture_output=True, text=True)
        assert result.returncode != 0
        assert "gh cli wrapper:" in result.stderr
        assert not log.exists()
        for denied in ("gh pr create -R ecamp/ecamp3 --repo ecamp/ecamp3",
                       "gh pr create -R bacluc-agent-orgevil/r",
                       "gh pr create --repo ecamp/ecamp3 --base devel --head bacluc-agent:issue-221-move-doctrine-validate-to-required-ci"):
            result = subprocess.run(["bash", "-c", denied], cwd=directory, env=env,
                                    capture_output=True, text=True)
            assert result.returncode != 0, denied
            assert "gh cli wrapper:" in result.stderr, denied
            assert not log.exists(), denied
        for argv in (["issue", "create", "-R", "bacluc/r", "--title", "x"],
                     ["repo", "fork", "ecamp/ecamp3", "--org", "bacluc-agent"],
                     ["workflow", "run", "ci.yml", "-R", "bacluc-agent-org/r"],
                     ["api", "repos/BacLuc/r/issues", "-XPOST"]):
            result = subprocess.run(["bash", "-c", 'exec gh "$@"', "test", *argv], cwd=directory,
                                    env=env, input="payload\n", capture_output=True, text=True)
            assert result.returncode == 23, result.stderr
            assert json.loads(result.stdout) == {"argv": argv, "stdin": "payload\n"}
            log.unlink()
        for argv in (["issue", "create", "-R", "ecamp/ecamp3", "--title", "x"],
                     ["repo", "fork", "ecamp/ecamp3", "--org", "outsider"],
                     ["workflow", "run", "ci.yml", "-R", "outsider/r"],
                     ["api", "repos/outsider/r/issues", "-XPOST"]):
            result = subprocess.run(["bash", "-c", 'exec gh "$@"', "test", *argv], cwd=directory,
                                    env=env, capture_output=True, text=True)
            assert result.returncode != 0, argv
            assert "gh cli wrapper:" in result.stderr, argv
            assert not log.exists(), argv
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

    def test_creation(self):
        self.call(["pr", "create", "-R", "ecamp/ecamp3", "--base", "devel", "--head",
                   "bacluc-agent:issue-221-move-doctrine-validate-to-required-ci"], False)
        self.call(["pr", "create", "-R", "bacluc-agent-org/r"])
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
        for argv in (["pr", "view", "10800", "-R", "ecamp/ecamp3"], ["search", "issues", "test"],
                     ["repo", "fork", "ecamp/ecamp3", "--org", "bacluc-agent"],
                     ["workflow", "run", "ci.yml", "--ref", "issue-234", "-R", "BacLuc/r"],
                     ["api", "search/issues", "-XGET", "-fq=repo:ecamp/ecamp3"],
                     ["api", "repos/ecamp/ecamp3/issues"]):
            self.call(argv, TEST_STATUS="17")
        for argv in (["issue", "comment", "234", "-R", "outsider/r", "--body", "pr create"],
                     ["api", "repos/ecamp/ecamp3/issues/1/comments", "-fbody=test"],
                     ["api", "repos/{owner}/{repo}/issues/comments/1", "-XPATCH", "-fbody=test"],
                     ["api", "repos/ecamp/ecamp3/forks", "-XPOST"],
                     ["api", "repos/ecamp/ecamp3/actions/workflows/ci.yml/dispatches", "-fref=main"]):
            self.call(argv, False)
        for argv in (["alias", "set", "p", "pr create"], ["extension", "exec", "p"], ["extensions", "exec", "p"],
                     ["custom"], ["--repo=BacLuc/r", "pr", "create"]):
            self.call(argv, False)
        self.call(["pr", "view"], False, TEST_ALIASES="pr: !echo bypass\n")
        self.call(["pr", "view"], False, TEST_ALIAS_STATUS="1")
        self.call(["pr", "view"], TEST_ALIASES="co: pr checkout\n")

    def test_issue_targets(self):
        for owner in ("bacluc", "bacluc-agent", "bacluc-agent-org"):
            for argv in (
                ["issue", "create", "-R", f"{owner}/r", "--title", "x"],
                ["issue", "comment", "1", "-R", f"{owner}/r", "--body", "x"],
                ["issue", "edit", "1", "-R", f"{owner}/r", "--title", "x"],
                ["issue", "close", "1", "-R", f"{owner}/r"],
                ["issue", "reopen", "1", "-R", f"{owner}/r"],
                ["issue", "lock", "1", "-R", f"{owner}/r"],
                ["issue", "unlock", "1", "-R", f"{owner}/r"],
                ["issue", "transfer", "1", f"{owner}/dest", "-R", f"{owner}/r"],
                ["issue", "-R", f"{owner}/r", "create", "--title", "x"],
            ):
                self.call(argv)
        for argv in (["issue", "list", "-R", "ecamp/ecamp3"],
                     ["issue", "view", "1", "-R", "ecamp/ecamp3"], ["issue", "--help"]):
            self.call(argv)
        for argv in (
            ["issue", "create", "--title", "x"],
            ["issue", "create", "-R", "ecamp/ecamp3", "--title", "x"],
            ["issue", "comment", "1", "-R", "outsider/r", "--body", "x"],
            ["issue", "edit", "1", "-R", "outsider/r", "--title", "x"],
            ["issue", "close", "1", "-R", "outsider/r"],
            ["issue", "reopen", "1", "-R", "outsider/r"],
            ["issue", "lock", "1", "-R", "outsider/r"],
            ["issue", "unlock", "1", "-R", "outsider/r"],
            ["issue", "transfer", "1", "ecamp/ecamp3", "-R", "BacLuc/r"],
            ["issue", "create", "-R", "BacLuc/r", "-R", "BacLuc/r", "--title", "x"],
            ["issue", "-R", "outsider/r", "create", "--title", "x"],
            ["issue", "--title", "x", "create"],
        ):
            self.call(argv, False)

    def test_repo_targets(self):
        for owner in ("bacluc", "bacluc-agent", "bacluc-agent-org"):
            for argv in (
                ["repo", "fork", "ecamp/ecamp3", "--org", owner],
                ["repo", "create", f"{owner}/r"],
                ["repo", "delete", f"{owner}/r"],
                ["repo", "edit", f"{owner}/r", "--description", "x"],
                ["repo", "archive", f"{owner}/r"],
                ["repo", "rename", "new-name", "-R", f"{owner}/r"],
                ["repo", "delete", "-R", f"{owner}/r"],
            ):
                self.call(argv)
        self.call(["repo", "view", "ecamp/ecamp3"])
        for argv in (
            ["repo", "fork", "ecamp/ecamp3", "--org", "outsider"],
            ["repo", "fork", "ecamp/ecamp3"],
            ["repo", "fork", "ecamp/ecamp3", "--org", "bacluc-agent", "--org", "bacluc"],
            ["repo", "create", "r"],
            ["repo", "create", "ecamp/r"],
            ["repo", "delete", "ecamp/r"],
            ["repo", "edit", "ecamp/r", "--description", "x"],
            ["repo", "archive", "ecamp/r"],
            ["repo", "rename", "new-name", "-R", "outsider/r"],
            ["repo", "rename", "new-name"],
            ["repo", "delete", "BacLuc/r", "-R", "BacLuc/r"],
            ["repo", "-R", "outsider/r", "delete"],
        ):
            self.call(argv, False)

    def test_workflow_targets(self):
        for owner in ("bacluc", "bacluc-agent", "bacluc-agent-org"):
            for argv in (
                ["workflow", "run", "ci.yml", "-R", f"{owner}/r"],
                ["workflow", "dispatch", "ci.yml", "-R", f"{owner}/r"],
                ["workflow", "enable", "ci.yml", "-R", f"{owner}/r"],
                ["workflow", "disable", "ci.yml", "-R", f"{owner}/r"],
                ["workflow", "-R", f"{owner}/r", "run", "ci.yml"],
            ):
                self.call(argv)
        for argv in (["workflow", "list", "-R", "ecamp/ecamp3"],
                     ["workflow", "view", "ci.yml", "-R", "ecamp/ecamp3"]):
            self.call(argv)
        for argv in (
            ["workflow", "run", "ci.yml"],
            ["workflow", "run", "ci.yml", "-R", "outsider/r"],
            ["workflow", "dispatch", "ci.yml", "-R", "outsider/r"],
            ["workflow", "enable", "ci.yml", "-R", "outsider/r"],
            ["workflow", "disable", "ci.yml", "-R", "outsider/r"],
            ["workflow", "-R", "outsider/r", "run", "ci.yml"],
            ["workflow", "run", "ci.yml", "-R", "BacLuc/r", "-R", "BacLuc/r"],
        ):
            self.call(argv, False)

    def test_discussion_label_release(self):
        for owner in ("bacluc", "bacluc-agent", "bacluc-agent-org"):
            for argv in (
                ["discussion", "create", "-R", f"{owner}/r", "--title", "x"],
                ["discussion", "comment", "1", "-R", f"{owner}/r", "--body", "x"],
                ["discussion", "edit", "1", "-R", f"{owner}/r", "--title", "x"],
                ["label", "create", "bug", "-R", f"{owner}/r", "--color", "fff"],
                ["label", "edit", "bug", "-R", f"{owner}/r", "--name", "x"],
                ["label", "delete", "bug", "-R", f"{owner}/r"],
                ["label", "clone", "ecamp/ecamp3", "-R", f"{owner}/r"],
                ["release", "create", "v1", "-R", f"{owner}/r"],
                ["release", "new", "v1", "-R", f"{owner}/r"],
                ["release", "edit", "v1", "-R", f"{owner}/r"],
                ["release", "delete", "v1", "-R", f"{owner}/r"],
                ["release", "upload", "v1", "file", "-R", f"{owner}/r"],
                ["release", "delete-asset", "v1", "file", "-R", f"{owner}/r"],
            ):
                self.call(argv)
        for argv in (["discussion", "list", "-R", "ecamp/ecamp3"],
                     ["label", "list", "-R", "ecamp/ecamp3"],
                     ["release", "list", "-R", "ecamp/ecamp3"],
                     ["release", "view", "v1", "-R", "ecamp/ecamp3"],
                     ["release", "download", "v1", "-R", "ecamp/ecamp3"],
                     ["release", "verify", "v1", "-R", "ecamp/ecamp3"]):
            self.call(argv)
        for argv in (
            ["discussion", "create", "-R", "outsider/r", "--title", "x"],
            ["discussion", "comment", "1", "-R", "outsider/r", "--body", "x"],
            ["discussion", "edit", "1", "-R", "outsider/r", "--title", "x"],
            ["label", "create", "bug", "-R", "outsider/r"],
            ["label", "edit", "bug", "-R", "outsider/r"],
            ["label", "delete", "bug", "-R", "outsider/r"],
            ["label", "clone", "ecamp/ecamp3", "-R", "outsider/r"],
            ["release", "create", "v1", "-R", "outsider/r"],
            ["release", "new", "v1", "-R", "outsider/r"],
            ["release", "edit", "v1", "-R", "outsider/r"],
            ["release", "delete", "v1", "-R", "outsider/r"],
            ["release", "upload", "v1", "file", "-R", "outsider/r"],
            ["release", "delete-asset", "v1", "file", "-R", "outsider/r"],
        ):
            self.call(argv, False)

    def test_secret_variable_ruleset_project(self):
        for owner in ("bacluc", "bacluc-agent", "bacluc-agent-org"):
            for argv in (
                ["secret", "set", "TOKEN", "-R", f"{owner}/r"],
                ["secret", "set", "TOKEN", "-o", owner],
                ["secret", "delete", "TOKEN", "-R", f"{owner}/r"],
                ["secret", "remove", "TOKEN", "-o", owner],
                ["variable", "set", "NAME", "-R", f"{owner}/r"],
                ["variable", "set", "NAME", "-o", owner],
                ["variable", "delete", "NAME", "-R", f"{owner}/r"],
                ["variable", "remove", "NAME", "-o", owner],
                ["ruleset", "create", "-R", f"{owner}/r"],
                ["ruleset", "update", "-R", f"{owner}/r"],
                ["ruleset", "delete", "-o", owner],
                ["project", "create", "--owner", owner, "--title", "x"],
                ["project", "copy", "1", "--target-owner", owner, "--title", "x"],
                ["project", "field-create", "1", "--owner", owner, "--name", "f"],
                ["project", "link", "1", "--owner", owner, "-R", f"{owner}/r"],
                ["project", "link", "1", "--owner", owner, "-R", "bare-name"],
                ["secret", "-o", owner, "set", "TOKEN"],
                ["project", "--owner", owner, "create", "--title", "x"],
            ):
                self.call(argv)
        for argv in (["secret", "list", "-R", "ecamp/ecamp3"],
                     ["secret", "list", "-o", "ecamp"],
                     ["variable", "list", "-R", "ecamp/ecamp3"],
                     ["variable", "get", "NAME", "-o", "ecamp"],
                     ["ruleset", "list", "-R", "ecamp/ecamp3"],
                     ["project", "view", "1", "--owner", "ecamp"],
                     ["project", "list", "--owner", "ecamp"]):
            self.call(argv)
        for argv in (
            ["secret", "set", "TOKEN"],
            ["secret", "set", "TOKEN", "-R", "outsider/r"],
            ["secret", "set", "TOKEN", "-o", "outsider"],
            ["secret", "set", "TOKEN", "-u"],
            ["secret", "delete", "TOKEN", "-R", "ecamp/ecamp3"],
            ["variable", "set", "NAME", "-R", "outsider/r"],
            ["variable", "delete", "NAME", "-o", "outsider"],
            ["ruleset", "create", "-R", "outsider/r"],
            ["ruleset", "update", "--org", "outsider"],
            ["ruleset", "delete"],
            ["project", "create", "--title", "x"],
            ["project", "create", "--owner", "outsider", "--title", "x"],
            ["project", "create", "--owner", "@me", "--title", "x"],
            ["project", "copy", "1", "--target-owner", "outsider", "--title", "x"],
            ["project", "copy", "1", "--source-owner", "bacluc", "--title", "x"],
            ["project", "field-delete", "1"],
            ["project", "field-delete", "1", "--id", "x"],
            ["project", "item-delete", "1", "--id", "x"],
            ["project", "link", "1", "--owner", "bacluc", "-R", "outsider/r"],
            ["project", "--owner", "outsider", "create", "--title", "x"],
            ["secret", "-o", "outsider", "set", "TOKEN"],
        ):
            self.call(argv, False)

    def test_api_mutation_repo_targets(self):
        for owner in ("bacluc", "bacluc-agent", "bacluc-agent-org"):
            for method in ("POST", "PATCH", "PUT", "DELETE"):
                self.call(["api", f"repos/{owner}/r/issues", "-X", method])
        for argv in (["api", "repos/ecamp/ecamp3/issues"],
                     ["api", "search/issues", "-XGET", "-fq=repo:ecamp/ecamp3"],
                     ["api", "orgs/ecamp"]):
            self.call(argv)
        for argv in (
            ["api", "repos/ecamp/ecamp3/issues", "-XPOST"],
            ["api", "repos/ecamp/ecamp3/issues", "-XPATCH"],
            ["api", "repos/ecamp/ecamp3/issues", "-XDELETE"],
            ["api", "search/issues", "-XPOST"],
            ["api", "orgs/bacluc/repos", "-XPOST"],
            ["api", "user/repos", "-XPOST"],
            ["api", "repositories/123", "-XPOST"],
            ["api", "repos/{owner}/{repo}/issues", "-XPOST"],
            ["api", "repos/BacLuc/r/pulls/../issues", "-XPOST"],
        ):
            self.call(argv, False)

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
