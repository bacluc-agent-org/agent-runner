import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).with_name("chatgpt-login-primitives-fallback.sh")


def _fake_uv(directory):
    (directory / "uv").write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
command=${3:-}
case "$command" in
  start)
    printf '%s\n' "$$" > "$PID_FILE"
    case "${START_MODE:-success}" in
      success) printf '%s\n' '{\"openai\":{\"type\":\"oauth\",\"access\":\"a\",\"refresh\":\"r\",\"expires\":1}}' ;;
      invalid) printf '%s\n' '{}' ;;
      retry) /bin/sleep 0.1 ;;
      cleanup)
        (trap '' TERM; while true; do /bin/sleep 1; done) &
        printf '%s\n' "$!" > "$CHILD_PID_FILE"
        trap '' TERM
        while true; do /bin/sleep 1; done
        ;;
    esac
    ;;
  status)
    printf '%s\n' "status" >> "$COMMAND_LOG"
    exit "${STATUS_CODE:-0}"
    ;;
  step)
    printf '%s\n' "step" >> "$COMMAND_LOG"
    count=$(wc -l < "$COMMAND_LOG")
    if [[ "${START_MODE:-}" == retry && "$count" -ge 2 ]]; then
      printf '%s\n' '{\"openai\":{\"type\":\"oauth\",\"access\":\"a\",\"refresh\":\"r\",\"expires\":1}}' > "$AUTH_FILE"
    fi
    exit "${STEP_CODE:-0}"
    ;;
  stop)
    printf '%s\n' "stop" >> "$COMMAND_LOG"
    ;;
esac
""",
        encoding="utf-8",
    )
    (directory / "uv").chmod(0o755)
    (directory / "gh").write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(stat -c '%a' "$AUTH_FILE")" == 600 ]]
printf '%s\n' "$AUTH_FILE" > "$STORED_FILE"
""",
        encoding="utf-8",
    )
    (directory / "gh").chmod(0o755)


def _run(tmp_path, mode, **extra):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _fake_uv(fake_bin)
    command_log = tmp_path / "commands.log"
    pid_file = tmp_path / "start.pid"
    child_pid_file = tmp_path / "child.pid"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "PID_FILE": str(pid_file),
        "CHILD_PID_FILE": str(child_pid_file),
        "COMMAND_LOG": str(command_log),
        "AUTH_FILE": str(tmp_path / "primitives-auth.json"),
        "STORED_FILE": str(tmp_path / "stored-file"),
        "START_MODE": mode,
        "BACLUC_AGENT_GITHUB_TOKEN": "token",
        "GITHUB_REPOSITORY": "bacluc-agent-org/agent-runner",
        **extra,
    }
    return subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True)


def test_success_stores_oauth_and_removes_restricted_temp_file(tmp_path):
    result = _run(tmp_path, "success")

    assert result.returncode == 0
    assert "stop" in (tmp_path / "commands.log").read_text()
    assert not (tmp_path / "primitives-auth.json").exists()
    assert (tmp_path / "stored-file").read_text().strip() == str(tmp_path / "primitives-auth.json")
    assert "Auth refreshed via primitives fallback" in result.stdout


def test_retryable_status_and_step_failure_eventually_succeeds(tmp_path):
    result = _run(tmp_path, "retry", STATUS_CODE="1", STEP_CODE="2")

    assert result.returncode == 0
    assert (tmp_path / "commands.log").read_text().count("status") >= 1
    assert (tmp_path / "commands.log").read_text().count("step") >= 1
    assert not (tmp_path / "primitives-auth.json").exists()


def test_invalid_json_fails_and_cleans_up(tmp_path):
    result = _run(tmp_path, "invalid")

    assert result.returncode != 0
    assert not (tmp_path / "primitives-auth.json").exists()
    assert "stop" in (tmp_path / "commands.log").read_text()


def test_cleanup_kills_stuck_browser_starter(tmp_path):
    result = _run(tmp_path, "cleanup", STATUS_CODE="3")

    assert result.returncode == 3
    assert "stop" in (tmp_path / "commands.log").read_text()
    pid = int((tmp_path / "start.pid").read_text())
    child_pid = int((tmp_path / "child.pid").read_text())
    assert subprocess.run(["kill", "-0", str(pid)], capture_output=True).returncode != 0
    assert subprocess.run(["kill", "-0", str(child_pid)], capture_output=True).returncode != 0
