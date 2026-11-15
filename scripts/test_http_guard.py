import contextlib
from datetime import datetime
import http.client
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import http_guard


ROOT = Path(__file__).resolve().parents[1]
OWNERS = ("bacluc", "bacluc-agent", "bacluc-agent-org")
MUTATIONS = ("POST", "PUT", "PATCH", "DELETE")


class HttpGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.log = self.directory / "http_guard_denials.jsonl"
        patcher = mock.patch.dict(os.environ, {"RUNNER_TEMP": str(self.directory)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def entries(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def assert_denied(self, method, url):
        with self.assertRaises(PermissionError):
            http_guard.check_request(method, url)

    def test_outsider_reads_are_allowed(self):
        for method in ("GET", "HEAD", "OPTIONS", "get"):
            for url in ("https://example.com/x", "http://example.com/x",
                        "https://api.github.com/repos/ecamp/ecamp3/issues",
                        "https://github.com/ecamp/ecamp3",
                        "https://api.github.com/search/issues?q=repo:ecamp/ecamp3",
                        "https://api.github.com:8443/repos/bacluc/r/issues"):
                self.assertIsNone(http_guard.check_request(method, url))
        self.assertEqual(self.entries(), [])

    def test_outsider_mutations_are_denied(self):
        for method in MUTATIONS:
            for url in ("https://example.com/x",
                        "https://api.github.com/repos/ecamp/ecamp3/issues",
                        "https://github.com/ecamp/ecamp3",
                        "https://uploads.github.com/repos/ecamp/ecamp3/releases/assets/1"):
                self.assert_denied(method, url)
        self.assertEqual([entry["method"] for entry in self.entries()], ["POST"] * 4)

    def test_owned_mutations_are_allowed(self):
        for owner in OWNERS:
            for method in MUTATIONS:
                for url in (f"https://api.github.com/repos/{owner}/r/issues",
                            f"https://github.com/{owner}/r",
                            f"https://uploads.github.com/repos/{owner}/r/releases/assets/1",
                            f"https://api.github.com./repos/{owner}/r/issues"):
                    self.assertIsNone(http_guard.check_request(method, url))
        self.assertEqual(self.entries(), [])

    def test_lookalike_hosts_and_owners_are_denied(self):
        for url in ("https://api.github.com.evil.com/repos/bacluc/r/issues",
                    "https://evilgithub.com/bacluc/r",
                    "https://api.github.com/repos/bacluc-evil/r/issues",
                    "https://api.github.com/repos/ecamp/r/issues",
                    "https://github.com/bacluc",
                    "https://github.com/repos/bacluc/r"):
            self.assert_denied("POST", url)

    def test_malformed_urls_are_denied_for_every_method(self):
        urls = ("https://api.github.com/repos/{owner}/{repo}/issues",
                "https://api.github.com/repos/bacluc/../issues",
                "https://api.github.com/repos/bacluc%2Fr/issues",
                "https://api.github.com/repos/bacluc%5Cr/issues",
                "https://api.github.com/repos/bacluc\\r/issues",
                "https://user:pass@api.github.com/repos/bacluc/r",
                "https://api.github.com:99999/repos/bacluc/r",
                "https://api.github.com:0/repos/bacluc/r",
                "ftp://api.github.com/repos/bacluc/r",
                "/repos/bacluc/r",
                "repos/bacluc/r",
                "https://api.github.com/repos//r/issues",
                "https://api.github.com/repos/bacluc/r/",
                "https://api.github.com/repos/bacluc/r/\x00issues")
        for method in ("GET", "POST"):
            for url in urls:
                self.assert_denied(method, url)
        for method in ("TRACE", "CONNECT", "BREW"):
            self.assert_denied(method, "https://api.github.com/repos/bacluc/r")

    def test_mutation_query_is_denied(self):
        self.assert_denied("POST", "https://api.github.com/repos/bacluc/r/issues?x=1")
        self.assertIsNone(http_guard.check_request(
            "GET", "https://api.github.com/search/issues?q=repo:bacluc/r"))

    def test_denial_log_schema_normalization_and_append(self):
        self.assert_denied("POST", "https://api.github.com:443/repos/ecamp/ecamp3/issues?foo=1#frag")
        self.assert_denied("POST", "https://example.com:8443/x?y=1")
        entries = self.entries()
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertEqual(set(entry), {"timestamp", "method", "host", "path", "reason"})
            self.assertEqual(entry["method"], "POST")
            self.assertIsInstance(entry["reason"], str)
            datetime.fromisoformat(entry["timestamp"])
        self.assertEqual(entries[0]["host"], "api.github.com")
        self.assertEqual(entries[0]["path"], "/repos/ecamp/ecamp3/issues")
        self.assertEqual(entries[1]["host"], "example.com")
        self.assertEqual(entries[1]["path"], "/x")

    def test_no_log_for_reads_non_post_or_owned_calls(self):
        http_guard.check_request("GET", "https://api.github.com/repos/ecamp/ecamp3/issues")
        self.assert_denied("PUT", "https://api.github.com/repos/ecamp/ecamp3/issues")
        self.assert_denied("DELETE", "https://example.com/x")
        http_guard.check_request("POST", "https://api.github.com/repos/bacluc/r/issues")
        self.assertEqual(self.entries(), [])

    def test_log_io_failure_still_denies(self):
        blocker = self.directory / "blocker"
        blocker.write_text("not a directory")
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"RUNNER_TEMP": str(blocker)}), contextlib.redirect_stderr(stderr):
            with self.assertRaises(PermissionError):
                http_guard.check_request("POST", "https://api.github.com/repos/ecamp/ecamp3/issues")
        self.assertIn("could not write denial log", stderr.getvalue())

    def test_cli_exit_codes(self):
        env = dict(os.environ, RUNNER_TEMP=str(self.directory))
        script = str(ROOT / "scripts/http_guard.py")
        allowed = subprocess.run([sys.executable, script, "GET", "https://example.com/x"],
                                 env=env, capture_output=True, text=True)
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(allowed.stdout, "ALLOW GET https://example.com/x\n")
        denied = subprocess.run([sys.executable, script, "POST", "https://example.com/x"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(denied.returncode, 1)
        self.assertTrue(denied.stderr.startswith("DENY: "), denied.stderr)
        owned = subprocess.run([sys.executable, script, "POST", "https://api.github.com/repos/BacLuc/r/issues"],
                               env=env, capture_output=True, text=True)
        self.assertEqual(owned.returncode, 0, owned.stderr)
        for argv in ([], ["GET"]):
            usage = subprocess.run([sys.executable, script, *argv], env=env, capture_output=True, text=True)
            self.assertEqual(usage.returncode, 2)
            self.assertIn("usage", usage.stderr)

    def test_import_does_not_patch_http_client(self):
        connection_init = http.client.HTTPConnection.__init__
        connect = http.client.HTTPSConnection.connect
        importlib.reload(http_guard)
        self.assertIs(http.client.HTTPConnection.__init__, connection_init)
        self.assertIs(http.client.HTTPSConnection.connect, connect)


if __name__ == "__main__":
    unittest.main()
