import os
import subprocess


ROOT = os.path.dirname(__file__)
WATCHDOG = os.path.join(ROOT, "coordinator-watchdog.sh")
WORKFLOW = os.path.join(ROOT, "..", ".github", "workflows", "opencode.yml")


def watchdog_source():
    source = open(WATCHDOG, encoding="utf-8").read()
    return (
        source.replace("total_seconds=$((total_minutes * 60))", "total_seconds=$((total_minutes))")
        .replace("idle_seconds=$((idle_minutes * 60))", "idle_seconds=$((idle_minutes))")
        .replace("sleep 30", "sleep 1")
    )


def run_watchdog(tmp_path, mode, total=10, idle=2):
    fake = tmp_path / "opencode"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "printf '%s\\n' $$ > \"$FAKE_PID_FILE\"\n"
        "case \"$FAKE_MODE\" in\n"
        " silent) while :; do :; done ;;\n"
        " periodic) for i in {1..4}; do printf 'progress %s\\n' \"$i\"; sleep 1; done ;;\n"
        " child) (sleep 30) & printf '%s\\n' $! > \"$FAKE_CHILD_FILE\"; wait ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    script = tmp_path / "watchdog.sh"
    script.write_text(watchdog_source(), encoding="utf-8")
    output = tmp_path / "output"
    status = tmp_path / "status"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "FAKE_MODE": mode,
        "FAKE_PID_FILE": str(tmp_path / "pid"),
        "FAKE_CHILD_FILE": str(tmp_path / "child"),
    }
    process = subprocess.run(
        [
            "setsid",
            "bash",
            str(script),
            str(output),
            str(total),
            str(idle),
            str(status),
            "coordinator",
            "model/test",
            "test",
            "prompt",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return process, status


def test_silent_output_is_killed_with_diagnostic(tmp_path):
    process, status = run_watchdog(tmp_path, "silent")
    assert process.returncode == 124
    assert status.read_text().strip() == "124"
    assert "zero-progress" in process.stderr
    assert "grace=2m" in process.stderr
    assert "model=model/test" in process.stderr
    assert "last_output_bytes=0" in process.stderr
    diagnostic = (tmp_path / "coordinator-diagnostic").read_text()
    assert "::error::coordinator zero-progress" in diagnostic
    assert "grace=2m" in diagnostic
    assert "model=model/test" in diagnostic
    assert "session=test" in diagnostic
    assert "last_output_bytes=0" in diagnostic
    assert (tmp_path / "coordinator-failure-mode").read_text().strip() == "zero-progress"
    assert not _alive(int((tmp_path / "pid").read_text()))


def test_periodic_output_completes_without_kill(tmp_path):
    process, status = run_watchdog(tmp_path, "periodic")
    assert process.returncode == 0
    assert status.read_text().strip() == "0"
    assert "progress 4" in (tmp_path / "output").read_text()
    assert "zero-progress" not in process.stderr
    assert not (tmp_path / "coordinator-diagnostic").exists()
    assert (tmp_path / "coordinator-failure-mode").read_text().strip() == ""


def test_silent_parent_with_live_child_is_spared(tmp_path):
    process, _ = run_watchdog(tmp_path, "child", total=3, idle=1)
    assert process.returncode == 124
    assert "total timeout" in process.stderr
    assert "zero-progress" not in process.stderr
    assert not (tmp_path / "coordinator-diagnostic").exists()
    assert (tmp_path / "coordinator-failure-mode").read_text().strip() == "timeout"
    assert not _alive(int((tmp_path / "child").read_text()))


def test_workflow_wires_failure_mode_and_diagnostic():
    workflow = open(WORKFLOW, encoding="utf-8").read()
    assert 'echo "failure_mode=$(cat "$RUNNER_TEMP/coordinator-failure-mode")" >> "$GITHUB_OUTPUT"' in workflow
    assert 'cat "$RUNNER_TEMP/coordinator-diagnostic"' in workflow


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
