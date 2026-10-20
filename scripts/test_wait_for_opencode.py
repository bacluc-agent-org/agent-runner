import os
from pathlib import Path
from subprocess import run

SCRIPT = Path(__file__).parents[1] / "scripts/wait_for_opencode.sh"

READY_STUB = """
calls_file="$RUNNER_TEMP/opencode-calls.txt"
printf 'call\\n' >> "$calls_file"
if (( $(($(wc -l < "$calls_file"))) < 2 )); then
  echo "plugin activation still running" >&2
  exit 1
fi
echo "models ok"
"""

FAILING_STUB = """
echo "DISTINCTIVE-PROBE-ERROR: Location plugins did not finish activating" >&2
exit 1
"""


def _run(tmp_path, stub_body, deadline):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "opencode"
    stub.write_text("#!/usr/bin/env bash\n" + stub_body + "\n")
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
    }
    return run(["bash", str(SCRIPT), str(deadline)], env=env, capture_output=True, text=True)


def test_becomes_ready_on_second_attempt(tmp_path):
    result = _run(tmp_path, READY_STUB, 30)
    assert result.returncode == 0, result.stderr
    assert "ready after" in result.stdout
    assert len((tmp_path / "opencode-calls.txt").read_text().splitlines()) == 2


def test_gives_up_at_deadline_and_cats_probe_output(tmp_path):
    result = _run(tmp_path, FAILING_STUB, 4)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "DISTINCTIVE-PROBE-ERROR" in combined
    assert "did not become ready" in combined
