import json
import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlparse

from http_guard import check_request, parse_nono_diagnostic


class HttpGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.previous = os.environ.get("RUNNER_TEMP")
        os.environ["RUNNER_TEMP"] = self.temp.name
        self.addCleanup(self.restore)
        self.log = Path(self.temp.name) / "http-guard-denials.jsonl"

    def restore(self):
        if self.previous is None:
            os.environ.pop("RUNNER_TEMP", None)
        else:
            os.environ["RUNNER_TEMP"] = self.previous

    def test_allowed(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            check_request(method, "https://outsider.example/x")
            check_request(method, "https://api.github.com/repos/ecamp/ecamp3/issues")
        for url in (
            "https://api.github.com/repos/bacluc-agent-org/r/issues",
            "https://api.github.com/repos/bacluc/r/pulls",
            "https://api.github.com/repos/octocat/Hello-World/forks",
            "https://uploads.github.com/repos/bacluc-agent/agent-runner/releases/1/assets",
        ):
            check_request("POST", url)
        self.assertFalse(self.log.exists())

    def test_denied(self):
        denied = [
            ("POST", "https://outsider.example/x", "host not allowlisted"),
            ("PUT", "https://outsider.example/x", "host not allowlisted"),
            ("PATCH", "https://outsider.example/x", "host not allowlisted"),
            ("DELETE", "https://outsider.example/x", "host not allowlisted"),
            ("POST", "https://outsider.example:8443/x", "host not allowlisted"),
            (
                "POST",
                "https://api.github.com/repos/ecamp/ecamp3/issues",
                "mutation outside owned repositories",
            ),
            (
                "POST",
                "https://api.github.com/repos/{owner}/{repo}/issues",
                "mutation outside owned repositories",
            ),
            (
                "POST",
                "https://uploads.github.com/repos/octocat/Hello-World/releases/1/assets",
                "mutation outside owned repositories",
            ),
        ]
        for method, url, _reason in denied:
            with self.assertRaises(PermissionError):
                check_request(method, url)
        lines = self.log.read_text().splitlines()
        self.assertEqual(len(lines), len(denied))
        for line, (method, url, reason) in zip(lines, denied):
            entry = json.loads(line)
            timestamp = entry.pop("timestamp")
            self.assertTrue(timestamp.endswith("+00:00"))
            datetime.fromisoformat(timestamp)
            parsed = urlparse(url)
            self.assertEqual(
                entry,
                {
                    "method": method,
                    "host": (parsed.hostname or "").lower(),
                    "path": parsed.path,
                    "reason": reason,
                },
            )

    def test_parse_nono_connect_denial(self):
        entry = parse_nono_diagnostic(
            "  deny connect example.com:443 (host example.com:443 is not in the allowlist)\n"
        )
        self.assertEqual(entry["method"], "CONNECT")
        self.assertEqual(entry["host"], "example.com")
        self.assertEqual(entry["path"], "")


if __name__ == "__main__":
    unittest.main()
