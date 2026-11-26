import os
import subprocess
import time

ROOT = os.path.dirname(__file__)
WATCHDOG = os.path.join(ROOT, "coordinator-watchdog.sh")
WORKFLOW = os.path.join(ROOT, "..", ".github", "workflows", "opencode.yml")

STUB_SOURCE = """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' $$ > "$FAKE_PID_FILE"
case "$FAKE_MODE" in
  silent) while :; do :; done ;;
  periodic) for i in {1..4}; do printf 'progress %s\\n' "$i"; sleep 1; done ;;
  child) (sleep 30) & printf '%s\\n' $! > "$FAKE_CHILD_FILE"; wait ;;
  storage) while :; do mkdir -p "$HOME/.local/share/opencode/storage"; printf x >> "$HOME/.local/share/opencode/storage/session"; sleep 1; done ;;
esac
"""


def watchdog_source():
    source = open(WATCHDOG, encoding="utf-8").read()
    return (
        source.replace("total_seconds=$((total_minutes * 60))", "total_seconds=$((total_minutes))")
        .replace("idle_seconds=$((idle_minutes * 60))", "idle_seconds=$((idle_minutes))")
        .replace("sleep 30", "sleep 1")
    )


def coordinator_block():
    lines = open(WORKFLOW, encoding="utf-8").read().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "- name: Run coordinator")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    block = []
    for line in lines[run + 1 :]:
        if line.strip() and not line.startswith("          "):
            break
        block.append(line[10:] if line.startswith("          ") else line)
    return "\n".join(block)


def run_block(tmp_path, mode, total=10, idle=2):
    runner_temp = tmp_path / "runner-temp"
    scripts = runner_temp / "agent-runner" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "coordinator-watchdog.sh").write_text(watchdog_source(), encoding="utf-8")
    (scripts / "progress-reporting.txt").write_text("progress instructions\n", encoding="utf-8")
    github_output = tmp_path / "github-output"
    github_output.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "opencode"
    stub.write_text(STUB_SOURCE, encoding="utf-8")
    stub.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(github_output),
        "MODEL": "model/test",
        "COORDINATOR_TIMEOUT": str(total),
        "NO_OUTPUT_TIMEOUT_MINUTES": str(idle),
        "VERIFY_WATCHDOG": "false",
        "PROMPT": "do the thing",
        "AGENT": "coordinator",
        "FAKE_MODE": mode,
        "FAKE_PID_FILE": str(tmp_path / "pid"),
        "FAKE_CHILD_FILE": str(tmp_path / "child"),
        "HOME": str(tmp_path),
    }
    started = time.monotonic()
    process = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", coordinator_block()],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return process, github_output, time.monotonic() - started, runner_temp


def test_silent_output_is_killed_with_diagnostic(tmp_path):
    process, output, elapsed, runner_temp = run_block(tmp_path, "silent")
    combined = process.stdout + process.stderr
    assert process.returncode == 124
    assert "failure_mode=zero-progress" in output.read_text()
    assert "coordinator_status=124" in output.read_text()
    assert combined.count("::error::coordinator zero-progress") == 1
    assert "::error::coordinator exceeded the" not in combined
    assert "grace=2m" in combined
    assert "model=model/test" in combined
    assert "session=coordinator-run" in combined
    assert "last_output_bytes=0" in combined
    assert elapsed < 8
    assert (runner_temp / "coordinator-failure-mode").read_text().strip() == "zero-progress"
    assert not _alive(int((tmp_path / "pid").read_text()))


def test_periodic_output_completes_without_kill(tmp_path):
    process, output, _, runner_temp = run_block(tmp_path, "periodic")
    combined = process.stdout + process.stderr
    assert process.returncode == 0
    assert "coordinator_status=0" in output.read_text()
    assert "progress 4" in (runner_temp / "coordinator.out").read_text()
    assert "zero-progress" not in combined
    assert not (runner_temp / "coordinator-diagnostic").exists()
    assert (runner_temp / "coordinator-failure-mode").read_text().strip() == ""


def test_silent_parent_with_live_child_is_spared(tmp_path):
    process, output, _, runner_temp = run_block(tmp_path, "child", idle=1)
    combined = process.stdout + process.stderr
    assert process.returncode == 124
    assert "failure_mode=timeout" in output.read_text()
    assert "total timeout" in combined
    assert "zero-progress" not in combined
    assert not (runner_temp / "coordinator-diagnostic").exists()
    assert not _alive(int((tmp_path / "child").read_text()))


def test_storage_activity_counts_as_progress(tmp_path):
    process, _, _, runner_temp = run_block(tmp_path, "storage")
    combined = process.stdout + process.stderr
    assert "zero-progress" not in combined
    assert (runner_temp / "coordinator-failure-mode").read_text().strip() == "timeout"


def test_workflow_wires_failure_mode_and_diagnostic():
    workflow = open(WORKFLOW, encoding="utf-8").read()
    assert '[[ -n "$failure_mode" ]] && echo "failure_mode=$failure_mode" >> "$GITHUB_OUTPUT"' in workflow
    assert 'cat "$RUNNER_TEMP/coordinator-diagnostic"' in workflow
    assert 'NO_OUTPUT_TIMEOUT_MINUTES: ${{ inputs.no_output_timeout_minutes || 15 }}' in workflow
    assert 'bash "$watchdog_script" "$RUNNER_TEMP/coordinator.out" "$COORDINATOR_TIMEOUT" "$NO_OUTPUT_TIMEOUT_MINUTES"' in workflow


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
