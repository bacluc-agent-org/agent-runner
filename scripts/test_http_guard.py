import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HttpGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = dict(os.environ, RUNNER_TEMP=self.temp.name,
                        PYTHONPATH=f"{ROOT / 'scripts'}:{os.environ.get('PYTHONPATH', '')}")

    def request(self, method, host, path="/"):
        code = (
            "import http.client, http_guard; "
            f"connection = http.client.HTTPConnection({host!r}); "
            "connection._send_request = lambda *args, **kwargs: None; "
            f"connection.request({method!r}, {path!r})"
        )
        return subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True)

    def test_outsider_reads_allowed_and_mutations_denied(self):
        for method in ("GET", "OPTIONS"):
            result = self.request(method, "outsider.example")
            self.assertNotIn("HTTP guard denied", result.stderr)
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            result = self.request(method, "outsider.example")
            self.assertNotEqual(result.returncode, 0, method)
            self.assertIn("HTTP guard denied", result.stderr, method)
        entries = [json.loads(row) for row in (Path(self.temp.name) / "http-guard.jsonl").read_text().splitlines()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0], {"method": "POST", "host": "outsider.example", "path": "/", "decision": "deny"})

    def test_owned_github_and_outsider_reads_allowed(self):
        for method, host, path in (("GET", "outsider.example", "/repo"),
                                   ("OPTIONS", "outsider.example", "/repo"),
                                   ("GET", "api.github.com", "/repos/bacluc-agent/agent-runner/pulls"),
                                   ("POST", "api.github.com", "/repos/bacluc-agent/agent-runner/issues")):
            result = self.request(method, host, path)
            self.assertNotIn("HTTP guard denied", result.stderr)


if __name__ == "__main__":
    unittest.main()