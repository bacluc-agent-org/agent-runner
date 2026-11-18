import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
FAKE = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if args == ["alias", "list"]:
    print(os.environ.get("TEST_ALIASES", ""), end="")
    sys.exit(int(os.environ.get("TEST_ALIAS_STATUS", "0")))
log = Path(os.environ["TEST_LOG"])
if args == ["api", "rate_limit"]:
    with Path(str(log) + ".probes").open("a") as probes:
        probes.write(json.dumps(args) + "\\n")
    print(os.environ.get("TEST_PROBE", ""), end="")
    sys.exit(int(os.environ.get("TEST_PROBE_STATUS", "0")))
with log.open("a") as handle:
    handle.write(json.dumps(args) + "\\n")
count_file = Path(str(log) + ".count")
count = int(count_file.read_text()) + 1 if count_file.exists() else 1
count_file.write_text(str(count))
if count <= int(os.environ.get("TEST_FAIL_COUNT", "0")):
    print(os.environ.get("TEST_FAIL_STDOUT", ""), end="")
    print(os.environ.get("TEST_FAIL_STDERR", ""), end="", file=sys.stderr)
    sys.exit(int(os.environ.get("TEST_FAIL_STATUS", "1")))
print(json.dumps({"argv": args, "stdin": sys.stdin.read()}))
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
                        TEST_LOG=str(self.directory / "log"), GH_HOST="github.com",
                        GH_CLI_WRAPPER_SLEEP_SECONDS="0")
        result = subprocess.run(["bash", str(ROOT / "scripts/install-gh-cli-wrapper.sh")],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.env["PATH"] = f"{(self.directory / 'path').read_text().strip()}:{self.env['PATH']}"

    def call(self, argv, allowed=True, **env):
        log = Path(self.env["TEST_LOG"])
        for suffix in ("", ".count", ".probes"):
            Path(str(log) + suffix).unlink(missing_ok=True)
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

    def run_gh(self, argv, **env):
        log = Path(self.env["TEST_LOG"])
        for suffix in ("", ".count", ".probes"):
            Path(str(log) + suffix).unlink(missing_ok=True)
        return subprocess.run(["bash", "-c", 'exec gh "$@"', "test", *argv],
                              cwd="/tmp", env=dict(self.env, **env), input="payload\n",
                              capture_output=True, text=True)

    def probe(self, **resources):
        return json.dumps({"resources": resources})

    def test_retry_then_success_replays_only_final_output(self):
        argv = ["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"]
        result = self.run_gh(argv, TEST_FAIL_COUNT="1", TEST_FAIL_STATUS="1",
                             TEST_FAIL_STDOUT="failed stdout\n",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n",
                             TEST_PROBE_STATUS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"argv": argv, "stdin": "payload\n"})
        self.assertEqual(result.stderr.count("backend stderr\n"), 1)
        self.assertNotIn("failed stdout", result.stdout)
        self.assertNotIn("rate limit exceeded", result.stderr)
        self.assertIn("::notice::gh cli wrapper: retrying gh issue view", result.stderr)
        log = Path(self.env["TEST_LOG"])
        self.assertEqual(len(log.read_text().splitlines()), 2)

    def test_search_bucket_waits_for_search_reset(self):
        reset = int(time.time()) + 30
        result = self.run_gh(["api", "search/issues", "-f", "q=test"], TEST_FAIL_COUNT="1",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n",
                             TEST_PROBE=self.probe(
                                 search={"remaining": 0, "reset": reset},
                                 core={"remaining": 5000, "reset": reset},
                                 graphql={"remaining": 5000, "reset": reset},
                             ))
        self.assertEqual(result.returncode, 0, result.stderr)
        probe_time = Path(self.env["TEST_LOG"] + ".probes").stat().st_mtime
        match = re.search(r"in ([0-9.]+)s \(attempt 1/3, search primary limit\)", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        self.assertAlmostEqual(float(match.group(1)), reset - probe_time, delta=1.0)

    def test_graphql_bucket_for_issue_view(self):
        reset = int(time.time()) + 12
        result = self.run_gh(["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"],
                             TEST_FAIL_COUNT="1",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n",
                             TEST_PROBE=self.probe(
                                 graphql={"remaining": 0, "reset": reset},
                                 core={"remaining": 5000, "reset": reset},
                                 search={"remaining": 5000, "reset": reset},
                             ))
        self.assertEqual(result.returncode, 0, result.stderr)
        probe_time = Path(self.env["TEST_LOG"] + ".probes").stat().st_mtime
        match = re.search(r"in ([0-9.]+)s \(attempt 1/3, graphql primary limit\)", result.stderr)
        self.assertIsNotNone(match, result.stderr)
        self.assertAlmostEqual(float(match.group(1)), reset - probe_time, delta=1.0)

    def test_secondary_limit_403_retried_without_probe(self):
        result = self.run_gh(["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"],
                             TEST_FAIL_COUNT="1",
                             TEST_FAIL_STDERR="HTTP 403: You have exceeded a secondary rate limit\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("attempt 1/3, graphql secondary limit", result.stderr)
        self.assertFalse(Path(self.env["TEST_LOG"] + ".probes").exists())

    def test_http_429_retried(self):
        result = self.run_gh(["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"],
                             TEST_FAIL_COUNT="1", TEST_FAIL_STDERR="HTTP 429: Too Many Requests\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("attempt 1/3, graphql secondary limit", result.stderr)

    def test_bad_credentials_not_retried(self):
        result = self.run_gh(["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"],
                             TEST_FAIL_COUNT="9", TEST_FAIL_STATUS="1",
                             TEST_FAIL_STDERR="HTTP 403: Bad credentials\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("::notice::", result.stderr)
        self.assertFalse(Path(self.env["TEST_LOG"] + ".probes").exists())
        self.assertEqual(len(Path(self.env["TEST_LOG"]).read_text().splitlines()), 1)

    def test_gives_up_after_max_attempts_and_preserves_exit_code(self):
        result = self.run_gh(["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"],
                             TEST_FAIL_COUNT="99", TEST_FAIL_STATUS="17",
                             TEST_FAIL_STDOUT="still limited\n",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n",
                             GH_CLI_WRAPPER_MAX_ATTEMPTS="3")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertEqual(result.stdout, "still limited\n")
        self.assertIn("still rate limited after 3 attempts", result.stderr)
        self.assertEqual(result.stderr.count("::notice::"), 2)
        self.assertEqual(len(Path(self.env["TEST_LOG"]).read_text().splitlines()), 3)

    def test_invalid_probe_json_falls_back(self):
        result = self.run_gh(["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"],
                             TEST_FAIL_COUNT="1",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n",
                             TEST_PROBE="not json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cannot read the GitHub rate limit", result.stderr)
        self.assertIn("in 5s (attempt 1/3, graphql primary limit)", result.stderr)

    def test_reset_beyond_cap_fails_fast(self):
        reset = int(time.time()) + 1000
        result = self.run_gh(["issue", "view", "1", "-R", "bacluc-agent-org/agent-runner"],
                             TEST_FAIL_COUNT="99", TEST_FAIL_STATUS="17",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n",
                             TEST_PROBE=self.probe(
                                 graphql={"remaining": 0, "reset": reset},
                                 core={"remaining": 5000, "reset": reset},
                                 search={"remaining": 5000, "reset": reset},
                             ),
                             GH_CLI_WRAPPER_MAX_SLEEP_SECONDS="60")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertIn("beyond the 60s retry cap", result.stderr)
        self.assertNotIn("::notice::", result.stderr)
        self.assertEqual(len(Path(self.env["TEST_LOG"]).read_text().splitlines()), 1)

    def test_stdin_replayed_across_retry(self):
        argv = ["issue", "comment", "1", "-R", "bacluc-agent-org/agent-runner",
                "--body-file", "-"]
        result = self.run_gh(argv, TEST_FAIL_COUNT="1",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"argv": argv, "stdin": "payload\n"})
        self.assertEqual(len(Path(self.env["TEST_LOG"]).read_text().splitlines()), 2)

    def test_streaming_run_watch_bypasses_retry(self):
        result = self.run_gh(["run", "watch", "123"], TEST_FAIL_COUNT="9",
                             TEST_FAIL_STATUS="1",
                             TEST_FAIL_STDERR="HTTP 403: API rate limit exceeded\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("::notice::", result.stderr)
        self.assertFalse(Path(self.env["TEST_LOG"] + ".probes").exists())
        self.assertEqual(len(Path(self.env["TEST_LOG"]).read_text().splitlines()), 1)

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
        self.call(["api", "graphql", "-f", "query=query { organization(login: owner) { projectV2(number: number) { id } } }"])
        self.call(["api", "graphql", "-f", "query=mutation { updateProjectV2ItemPosition(input: {projectId: id, itemId: id, afterId: null}) { projectV2Item { id } } }"])
        self.call(["api", "graphql", "-f", "query=query { repository(owner: owner, name: name) { id } }"] , False)
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
