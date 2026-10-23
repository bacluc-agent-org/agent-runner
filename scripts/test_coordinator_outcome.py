import json
import os
import subprocess
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/opencode.yml"


def _outcome_block():
    text = WORKFLOW.read_text()
    return text[text.index("# coordinator-outcome-start") : text.index("# coordinator-outcome-end")]


def _run_outcome(tmp_path, events, status, fatal=False):
    jsonl = tmp_path / "coordinator.jsonl"
    jsonl.write_text("".join(json.dumps(event) + "\n" for event in events))
    if fatal:
        (tmp_path / "opencode-fatal.json").write_text('{"reason": "auth", "model": "opencode/big-pickle"}')
    script = (
        f"RUNNER_TEMP={str(tmp_path)!r}\n"
        f"coordinator_jsonl={str(jsonl)!r}\n"
        f"coordinator_status={status}\n"
        "trap 'status=$?; printf \"outcome=%s failure=%s\\n\" \"$coordinator_outcome\" \"$coordinator_failure\"; exit \"$status\"' EXIT\n"
        f"{_outcome_block()}\n"
    )
    return subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", script],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
        text=True,
    )


TEXT = {"type": "text", "part": {"text": "done"}}


def test_nonzero_status_with_final_text_is_ok(tmp_path):
    result = _run_outcome(tmp_path, [TEXT], status=1)
    assert "outcome=ok failure=" in result.stdout


def test_zero_status_with_final_text_is_ok(tmp_path):
    result = _run_outcome(tmp_path, [TEXT], status=0)
    assert "outcome=ok failure=" in result.stdout


def test_nonzero_status_with_only_endpoint_error_is_no_final_message(tmp_path):
    result = _run_outcome(
        tmp_path,
        [{"type": "error", "error": {"message": "Endpoint baseURL not found"}}],
        status=1,
    )
    assert "outcome=fail failure=no final assistant message" in result.stdout


def test_nonzero_status_with_agent_not_found_error_fails(tmp_path):
    result = _run_outcome(
        tmp_path,
        [{"type": "error", "error": {"message": 'Agent not found: "coordinator"'}}],
        status=1,
    )
    assert "outcome=fail" in result.stdout


def test_timeout_status_with_text_fails_as_timeout(tmp_path):
    result = _run_outcome(tmp_path, [TEXT], status=124)
    assert "outcome=fail failure=timeout" in result.stdout


def test_zero_status_with_empty_output_is_no_final_message(tmp_path):
    result = _run_outcome(tmp_path, [], status=0)
    assert "outcome=fail failure=no final assistant message" in result.stdout


def test_fatal_provider_marker_beats_a_final_text(tmp_path):
    result = _run_outcome(tmp_path, [TEXT], status=1, fatal=True)
    assert "outcome=fail failure=fatal provider error" in result.stdout


def test_workflow_runs_the_coordinator_in_json_format():
    assert "--format json" in WORKFLOW.read_text()


def test_workflow_redirects_coordinator_output_instead_of_piping_through_tee():
    content = WORKFLOW.read_text()
    assert '| tee "$coordinator_jsonl"' not in content
    assert '> "$coordinator_jsonl"' in content
