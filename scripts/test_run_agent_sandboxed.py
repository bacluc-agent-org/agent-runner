import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


class RunAgentSandboxedTest(unittest.TestCase):
    def test_preserves_stderr_and_status_and_parses_nono_denial(self):
        with tempfile.TemporaryDirectory() as temp:
            bin_dir = Path(temp) / "bin"
            bin_dir.mkdir()
            fake_nono = bin_dir / "nono"
            fake_nono.write_text(
                "#!/bin/sh\nprintf '%s\\n' 'endpoint rules denied POST https://example.com/write' >&2\nexit 23\n"
            )
            fake_nono.chmod(0o755)
            env = os.environ | {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "NONO_PROFILE": "profile.json",
                "RUNNER_TEMP": temp,
            }
            result = subprocess.run(
                ["scripts/run-agent-sandboxed.sh", "probe"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 23)
            self.assertIn("endpoint rules denied POST https://example.com/write", result.stderr)
            self.assertIn("endpoint rules denied POST https://example.com/write", (Path(temp) / "nono-proxy.log").read_text())
            entries = [json.loads(line) for line in (Path(temp) / "http-guard-denials.jsonl").read_text().splitlines()]
            self.assertEqual(entries[0]["method"], "POST")
            self.assertEqual(entries[0]["host"], "example.com")
            self.assertEqual(entries[0]["path"], "/write")


if __name__ == "__main__":
    unittest.main()
