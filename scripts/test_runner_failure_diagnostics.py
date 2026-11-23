import os
import re
import subprocess

ROOT = os.path.dirname(__file__)
WORKFLOW = os.path.join(ROOT, "..", ".github", "workflows", "opencode.yml")


def coordinator_block():
    lines = open(WORKFLOW, encoding="utf-8").read().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "- name: Run coordinator")
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body = []
    for line in lines[run + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip(" "))) < 10:
            break
        body.append(line[10:] if line.startswith("          ") else line)
    return "\n".join(body)


def fast_block():
    block = coordinator_block()
    assert "sleep 30" in block
    assert "idle_seconds=$((NO_OUTPUT_TIMEOUT * 60))" in block
    assert "total_seconds=$((COORDINATOR_TIMEOUT * 60))" in block
    return (
        block.replace("sleep 30", "sleep 1")
        .replace("idle_seconds=$((NO_OUTPUT_TIMEOUT * 60))", "idle_seconds=$((NO_OUTPUT_TIMEOUT))")
        .replace("total_seconds=$((COORDINATOR_TIMEOUT * 60))", "total_seconds=$((COORDINATOR_TIMEOUT))")
    )


def run_coordinator(tmp_path, mode, total=10, idle=2):
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
    scripts = tmp_path / "agent-runner" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "progress-reporting.txt").write_text("progress reporting\n", encoding="utf-8")
    github_output = tmp_path / "github-output"
    github_output.touch()
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(github_output),
        "MODEL": "model/test",
        "AGENT": "coordinator",
        "COORDINATOR_TIMEOUT": str(total),
        "NO_OUTPUT_TIMEOUT": str(idle),
        "PROMPT": "test prompt",
        "VERIFY_WATCHDOG": "false",
        "FAKE_MODE": mode,
        "FAKE_PID_FILE": str(tmp_path / "pid"),
        "FAKE_CHILD_FILE": str(tmp_path / "child"),
    }
    process = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", fast_block()],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return process, github_output


def test_silent_output_is_killed_with_diagnostic(tmp_path):
    process, github_output = run_coordinator(tmp_path, "silent")
    assert process.returncode == 124
    assert (tmp_path / "coordinator-status").read_text().strip() == "124"
    assert "zero-progress" in process.stderr
    assert "grace=" in process.stderr
    assert "model=model/test" in process.stderr
    assert "session=coordinator-run" in process.stderr
    assert "last_output_bytes=0" in process.stderr
    assert re.search(r"after \d+m", process.stderr)
    diagnostic = (tmp_path / "coordinator-diagnostic").read_text()
    assert "::error::coordinator zero-progress" in diagnostic
    assert "grace=" in diagnostic
    assert "model=model/test" in diagnostic
    assert "session=coordinator-run" in diagnostic
    assert "last_output_bytes=0" in diagnostic
    assert (tmp_path / "coordinator-failure-mode").read_text().strip() == "zero-progress"
    assert "failure_mode=zero-progress" in github_output.read_text()
    assert "coordinator_status=124" in github_output.read_text()
    assert not _alive(int((tmp_path / "pid").read_text()))


def test_fence_closes_before_diagnostic_is_printed(tmp_path):
    block = coordinator_block()
    assert block.index('cat "$RUNNER_TEMP/coordinator-diagnostic"') > block.index(
        "printf '::%s::\\n' \"$stop_token\""
    )
    process, _ = run_coordinator(tmp_path, "silent")
    token = re.search(r"::stop-commands::([0-9a-f]+)", process.stdout).group(1)
    assert f"\n::{token}::\n" in process.stdout
    assert process.stdout.index(f"\n::{token}::\n") < process.stdout.index(
        "::error::coordinator zero-progress"
    )


def test_periodic_output_completes_without_kill(tmp_path):
    process, github_output = run_coordinator(tmp_path, "periodic")
    assert process.returncode == 0
    assert (tmp_path / "coordinator-status").read_text().strip() == "0"
    assert "progress 4" in (tmp_path / "coordinator.out").read_text()
    assert "progress 4" in process.stdout
    assert not (tmp_path / "coordinator-diagnostic").exists()
    assert (tmp_path / "coordinator-failure-mode").read_text().strip() == ""
    assert not re.search(r"^failure_mode=.+$", github_output.read_text(), re.M)


def test_silent_parent_with_live_child_is_spared(tmp_path):
    process, _ = run_coordinator(tmp_path, "child", total=3, idle=1)
    assert process.returncode == 124
    assert "total timeout" in process.stderr
    assert "zero-progress" not in process.stderr
    assert (tmp_path / "coordinator-failure-mode").read_text().strip() == "timeout"
    assert not (tmp_path / "coordinator-diagnostic").exists()
    assert not _alive(int((tmp_path / "child").read_text()))


def test_invalid_timeouts_are_rejected_before_fence(tmp_path):
    process, _ = run_coordinator(tmp_path, "silent", total=0)
    assert process.returncode == 2
    assert "Coordinator timeouts must be positive integers" in process.stderr
    assert "::stop-commands::" not in process.stdout


def test_workflow_wires_inputs_outputs_and_guards():
    workflow = open(WORKFLOW, encoding="utf-8").read()
    assert len(re.findall(r"^      no_output_timeout_minutes:\s*$", workflow, re.M)) == 2
    assert workflow.count("default: 15") == 2
    assert "NO_OUTPUT_TIMEOUT: ${{ inputs.no_output_timeout_minutes || 15 }}" in workflow
    assert "timeout-minutes: 130" in workflow
    assert "printf 'no_output_timeout_minutes: %s\\n' \"$NO_OUTPUT_TIMEOUT_MINUTES\"" in workflow
    assert "printf 'verify_watchdog: %s\\n' \"$VERIFY_WATCHDOG\"" in workflow
    assert "Coordinator statuses: opencode=%s tee=0" in workflow
    assert "coordinator_tee_status=0" in workflow
    assert workflow.count("inputs.verify_watchdog != true") == 2
    assert "VERIFY_WATCHDOG: ${{ inputs.verify_watchdog }}" in workflow


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
