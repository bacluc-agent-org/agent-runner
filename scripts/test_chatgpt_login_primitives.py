import json
import os
import subprocess
import time
from pathlib import Path


SCRIPT = Path(__file__).with_name("chatgpt-login-primitives-fallback.sh")


def run_fallback(tmp_path, mode, timeout=7):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uv").write_text(
        """#!/usr/bin/env bash
set -eu
mode=${MODE:?}
if [[ ${3:-} != status ]]; then
  printf 'uv %s %s %s %s\\n' "${1:-}" "${2:-}" "${3:-}" "${4:-}" >> "$LOG_FILE"
fi
if [[ ${3:-} == start ]]; then
  if [[ $mode == secret-action ]]; then
    while [[ ! -e "$RUNNER_TEMP/chatgpt-login-recovery.actions" ]]; do sleep 1; done
    printf '%s\\n' '{"openai":{"type":"oauth","access":"access","refresh":"refresh","expires":1}}'
  elif [[ $mode == cleanup-hang ]]; then
    trap '' TERM
    while :; do sleep 1; done
  else
    trap 'exit 0' TERM
    while :; do sleep 1; done
  fi
elif [[ ${3:-} == status ]]; then
  printf 'status\\n' >> "$LOG_FILE"
  if [[ $mode == secret-action && -e "$RUNNER_TEMP/chatgpt-login-recovery.actions" ]]; then
    printf '%s\\n' '{"step":"authorize","code_ready":true}'
  elif [[ $mode == step-hang ]]; then
    printf '%s\\n' '{"step":"authorize"}'
  else
    printf '%s\\n' '{"step":"authorize","blocking":"login"}'
  fi
  if [[ $mode == status-hang ]]; then sleep 30; fi
elif [[ ${3:-} == stop ]]; then
  printf 'stop %s %s\\n' "${CHATGPT_PASSWORD:-}" "${BACLUC_AGENT_GITHUB_TOKEN:-}" >> "$LOG_FILE"
  [[ $mode == stop-hang ]] && sleep 30
elif [[ ${3:-} == step ]]; then
  [[ $mode != step-hang ]] || sleep 30
  exit 0
fi
""",
        encoding="utf-8",
    )
    (bin_dir / "playwright-cli").write_text(
        """#!/usr/bin/env bash
set -eu
printf 'playwright %s %s %s\\n' "$1" "${CHATGPT_PASSWORD:-}" "${BACLUC_AGENT_GITHUB_TOKEN:-}" >> "$LOG_FILE"
case "${2:-}" in
  attach) [[ $MODE != attach-hang ]] || sleep 30 ;;
  detach) [[ $MODE != detach-hang ]] || sleep 30 ;;
esac
""",
        encoding="utf-8",
    )
    (bin_dir / "opencode").write_text(
        """#!/usr/bin/env bash
set -eu
printf 'opencode %s %s\\n' "${CHATGPT_PASSWORD:-}" "${BACLUC_AGENT_GITHUB_TOKEN:-}" >> "$LOG_FILE"
case "$MODE" in
  invalid-url|detach-hang|stop-hang|cleanup-hang) printf '%s\\n' '{"action":"goto","url":"https://evil.example/"}' > "$RUNNER_TEMP/chatgpt-login-recovery.actions" ;;
  secret-action) printf '%s\\n' '{"action":"fill_secret","ref":"e1","kind":"email"}' > "$RUNNER_TEMP/chatgpt-login-recovery.actions" ;;
esac
[[ $MODE == secret-action || $MODE == invalid-url || $MODE == detach-hang || $MODE == stop-hang || $MODE == cleanup-hang ]] || exit 1
""",
        encoding="utf-8",
    )
    (bin_dir / "gh").write_text(
        """#!/usr/bin/env bash
set -eu
printf 'gh %s %s\\n' "${CHATGPT_PASSWORD:-}" "${BACLUC_AGENT_GITHUB_TOKEN:-}" >> "$LOG_FILE"
""",
        encoding="utf-8",
    )
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)
    log_file = tmp_path / "log"
    log_file.touch()
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{bin_dir}:{environment['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "CHATGPT_EMAIL": "user@example.com",
            "CHATGPT_PASSWORD": "private-password",
            "BACLUC_AGENT_GITHUB_TOKEN": "private-token",
            "PLAYWRIGHT_HANDOFF_TIMEOUT_SECONDS": "2",
            "PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS": "20",
            "MODE": mode,
            "LOG_FILE": str(log_file),
        }
    )
    started = time.monotonic()
    result = subprocess.run(
        [str(SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result, time.monotonic() - started, log_file.read_text(encoding="utf-8")


def test_rejects_untrusted_url_action(tmp_path):
    result, _, _ = run_fallback(tmp_path, "invalid-url")
    assert result.returncode != 0


def test_keeps_secrets_out_of_handoff_processes(tmp_path):
    result, _, log = run_fallback(tmp_path, "secret-action")
    assert result.returncode == 0
    handoff_log = "\n".join(line for line in log.splitlines() if not line.startswith("gh "))
    assert "private-password" not in handoff_log
    assert "private-token" not in handoff_log


def test_hanging_attach_expires_at_handoff_deadline(tmp_path):
    result, elapsed, _ = run_fallback(tmp_path, "attach-hang")
    assert result.returncode != 0
    assert elapsed < 5


def test_hanging_detach_expires_at_handoff_deadline(tmp_path):
    result, elapsed, log = run_fallback(tmp_path, "detach-hang")
    assert result.returncode != 0
    assert elapsed < 5
    assert "playwright -s=chatgpt-auth-recovery" in log


def test_status_and_cleanup_stop_share_handoff_deadline(tmp_path):
    result, elapsed, log = run_fallback(tmp_path, "status-hang")
    assert result.returncode != 0
    assert elapsed < 5
    assert "stop" not in log


def test_cleanup_stop_is_bounded_after_recovery_failure(tmp_path):
    result, elapsed, log = run_fallback(tmp_path, "stop-hang")
    assert result.returncode != 0
    assert elapsed < 5
    assert "stop" in log


def test_step_is_bounded_by_handoff_deadline(tmp_path):
    result, elapsed, _ = run_fallback(tmp_path, "step-hang")
    assert result.returncode != 0
    assert elapsed < 5


def test_cleanup_escalates_when_startup_ignores_term(tmp_path):
    result, elapsed, log = run_fallback(tmp_path, "cleanup-hang")
    assert result.returncode != 0
    assert elapsed < 5
    assert "stop" in log
