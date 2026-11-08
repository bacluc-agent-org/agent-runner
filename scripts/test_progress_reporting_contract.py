from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_progress_reporting_contains_coordinator_completion_contract():
    contract = (ROOT / "scripts/progress-reporting.txt").read_text()

    assert "Coordinator completion contract:" in contract
    assert "do not report success" in contract
    assert "state it plainly" in contract


def test_opencode_workflow_injects_progress_reporting_contract():
    workflow = (ROOT / ".github/workflows/opencode.yml").read_text()

    assert 'cat "$RUNNER_TEMP/agent-runner/scripts/progress-reporting.txt"' in workflow
    assert 'PROMPT=$(printf' in workflow
