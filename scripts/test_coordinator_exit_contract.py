import os
import pathlib
import re
import stat
import subprocess

import run_status

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/opencode.yml"


def extract_run_block(step_name):
    lines = WORKFLOW.read_text().splitlines()
    match = re.compile(r"^(\s*)-\s+name:\s*" + re.escape(step_name) + r"\s*$")
    start = next(i for i, line in enumerate(lines) if match.match(line))
    key_indent = len(match.match(lines[start]).group(1)) + 2
    run_match = re.compile(r"^\s{%d}run:\s*[|>]" % key_indent)
    run_start = next(i for i in range(start + 1, len(lines)) if run_match.match(lines[i]))
    body = []
    base = None
    for line in lines[run_start + 1 :]:
        if not line.strip():
            body.append("")
            continue
        indent = len(line) - len(line.lstrip())
        if indent < key_indent:
            break
        if base is None:
            base = indent
        elif indent < base:
            break
        body.append(line)
    while body and not body[-1].strip():
        body.pop()
    assert base is not None
    return "\n".join(line[base:] if line.strip() else "" for line in body)


def run_coordinator_block(script, tmp_path, coordinator_exit=0):
    runner_temp = tmp_path / "runner-temp"
    scripts = runner_temp / "agent-runner/scripts"
    scripts.mkdir(parents=True)
    (scripts / "progress-reporting.txt").write_text("test\n")
    (runner_temp / "coordinator.out").mkdir()
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    stub = stub_bin / "opencode"
    stub.write_text(f"#!/usr/bin/env bash\nprintf 'coordinator\\n'\nexit {coordinator_exit}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    output = runner_temp / "github-output"
    output.touch()
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", script],
        env={
            **os.environ,
            "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(output),
            "PROMPT": "test",
            "MODEL": "opencode/test",
            "COORDINATOR_TIMEOUT": "1",
        },
        capture_output=True,
        text=True,
    )
    result.outputs = dict(
        line.split("=", 1) for line in output.read_text().splitlines() if "=" in line
    )
    return result


def test_successful_coordinator_with_failing_tee_exits_successfully(tmp_path):
    result = run_coordinator_block(extract_run_block("Run coordinator"), tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.outputs == {"coordinator_status": "0", "coordinator_tee_status": "1"}


def test_coordinator_failure_is_preserved(tmp_path):
    result = run_coordinator_block(
        extract_run_block("Run coordinator"), tmp_path, coordinator_exit=7
    )
    assert result.returncode == 7, result.stderr
    assert result.outputs["coordinator_status"] == "7"


def test_workflow_wires_numeric_statuses_into_classifier():
    workflow = WORKFLOW.read_text()
    step = extract_run_block("Post run-result comment")
    assert 'python3 "$RUNNER_TEMP/agent-runner/scripts/run_status.py"' in step
    assert "COORDINATOR_STATUS: ${{ steps.coordinator.outputs.coordinator_status }}" in workflow
    assert "COORDINATOR_TEE_STATUS: ${{ steps.coordinator.outputs.coordinator_tee_status }}" in workflow
    assert "COORDINATOR_OUTCOME: ${{ steps.coordinator.outcome }}" in workflow
    assert run_status.classify("0", "1", "failure", "") == (
        "✅ completed (log capture failed, tee exit 1)"
    )
