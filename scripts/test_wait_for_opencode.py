#!/usr/bin/env python3
"""Regression tests for scripts/wait_for_opencode.sh: capture on failure, exit codes, per-attempt timeout.

The script is run through a PATH shim with a fake `opencode`; the hang test also shims
`timeout` (the script resolves it via PATH too) to cap its 30s probe at 2s.
Stdlib only on purpose: runs with plain `python3`, matching scripts/test_gh_cli_wrapper.py.
"""

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "wait_for_opencode.sh"


class WaitForOpenCodeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.bin_dir = os.path.join(tmp.name, "bin")
        os.mkdir(self.bin_dir)

    def _shim(self, name, body):
        path = os.path.join(self.bin_dir, name)
        with open(path, "w") as f:
            f.write("#!/usr/bin/env bash\n" + body + "\n")
        os.chmod(path, 0o755)

    def _run(self):
        env = dict(os.environ)
        env["PATH"] = self.bin_dir + os.pathsep + env["PATH"]
        env["RUNNER_TEMP"] = os.path.dirname(self.bin_dir)
        return subprocess.run(["bash", str(SCRIPT), "2"], env=env,
                              capture_output=True, text=True, timeout=120)

    def test_success(self):
        self._shim("opencode", "exit 0")
        result = self._run()
        self.assertEqual(result.returncode, 0)
        self.assertIn("OpenCode CLI ready after 1 attempt(s)", result.stdout)

    def test_failure_captures_last_probe_output(self):
        self._shim("opencode", "echo probe-stderr >&2; echo probe-stdout; exit 3")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not become ready after 2 attempts", result.stderr)
        self.assertIn("probe-stderr", result.stderr)
        self.assertIn("probe-stdout", result.stderr)

    def test_hung_probe_cannot_stall_forever(self):
        self._shim("opencode", "echo hung >&2; sleep 300")
        # The script calls `timeout 30 opencode models`; shim caps the 30s at 2s.
        self._shim("timeout", 'exec /usr/bin/timeout 2 "${@:2}"')
        start = time.monotonic()
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("did not become ready after 2 attempts", result.stderr)
        self.assertIn("hung", result.stderr)
        self.assertLess(time.monotonic() - start, 60)


if __name__ == "__main__":
    unittest.main()
