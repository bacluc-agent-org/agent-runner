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

RECORDING_FAILING_STUB = """
printf 'call\\n' >> "$RUNNER_TEMP/opencode-calls.txt"
echo "DISTINCTIVE-PROBE-ERROR: Location plugins did not finish activating" >&2
exit 1
"""


def _run(tmp_path, stub_body, deadline=None, instant_sleep=False, wall_timeout=None):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "opencode"
    stub.write_text("#!/usr/bin/env bash\n" + stub_body + "\n")
    stub.chmod(0o755)
    if instant_sleep:
        sleep_stub = bindir / "sleep"
        sleep_stub.write_text("#!/usr/bin/env bash\nexit 0\n")
        sleep_stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": "",
    }
    args = ["bash", str(SCRIPT)]
    if deadline is not None:
        args.append(str(deadline))
    if wall_timeout is not None:
        args = ["timeout", str(wall_timeout), *args]
    return run(args, env=env, capture_output=True, text=True)


def _attempts(tmp_path):
    return len((tmp_path / "opencode-calls.txt").read_text().splitlines())


def test_becomes_ready_on_second_attempt(tmp_path):
    result = _run(tmp_path, READY_STUB, 30)
    assert result.returncode == 0, result.stderr
    assert "ready after" in result.stdout
    assert _attempts(tmp_path) == 2


def test_gives_up_at_deadline_and_cats_probe_output(tmp_path):
    result = _run(tmp_path, FAILING_STUB, 4)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "DISTINCTIVE-PROBE-ERROR" in combined
    assert "did not become ready within 4s" in combined


def test_deadline_is_a_wall_clock_budget_not_an_attempt_count(tmp_path):
    result = _run(tmp_path, RECORDING_FAILING_STUB, 3, instant_sleep=True)
    assert result.returncode == 1
    assert "did not become ready within 3s" in result.stdout + result.stderr
    assert _attempts(tmp_path) > 3


def test_default_budget_is_600_seconds(tmp_path):
    result = _run(tmp_path, FAILING_STUB, wall_timeout=2)
    assert result.returncode == 124
    assert "up to 600s" in result.stdout


def test_surfaces_plugin_install_errors_but_not_the_rest_of_the_log(tmp_path):
    log = tmp_path / ".local/share/opencode/log/opencode.log"
    log.parent.mkdir(parents=True)
    log.write_text(
        'timestamp=x level=WARN message="failed to load plugin" '
        'target=opencode-auto-resume@1.1.21 cause="Cause([Fail(PluginModule.LoadError: bad default export)])"\n'
        'timestamp=x level=INFO message="spawning process" args="SECRET-AGENT-PROMPT"\n'
        'timestamp=x level=INFO message="spawning process" args="grep failed to load plugin.*NpmInstallFailedError SECRET-ARGS-MARKER"\n'
        'timestamp=x level=WARN message="failed to load plugin" '
        'target=@dietrichgebert/ponytail@5.3.2 cause="Cause([Fail(NpmInstallFailedError '
        '(cause: TypeError [ERR_INVALID_PROTOCOL]))])"\n'
    )
    result = _run(tmp_path, FAILING_STUB, 0)
    assert result.returncode == 1
    assert "NpmInstallFailedError" in result.stderr
    assert "ERR_INVALID_PROTOCOL" in result.stderr
    assert "SECRET-AGENT-PROMPT" not in result.stderr
    assert "SECRET-ARGS-MARKER" not in result.stderr
    assert "PluginModule.LoadError" not in result.stderr
