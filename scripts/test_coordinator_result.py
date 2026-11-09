import coordinator_result
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/opencode.yml"


def test_success_ignores_tee_failure():
    assert coordinator_result.classify(0, 1) == ("✅ completed", "success")


def test_timeout_is_classified_from_coordinator_status():
    assert coordinator_result.classify(124, 1) == ("⚠️ failed (coordinator timeout)", "timeout")


def test_other_nonzero_is_detected():
    assert coordinator_result.classify(17, 1) == ("⚠️ failed (coordinator exit 17)", "error")


def test_fatal_reason_takes_precedence():
    assert coordinator_result.classify(0, 1, "balance") == ("❌ fatal error: balance", "error")


def test_workflow_uses_numeric_coordinator_outputs():
    workflow = WORKFLOW.read_text()
    assert 'exit 0' in workflow
    assert 'exit "$coordinator_tee_status"' not in workflow
    assert 'COORDINATOR_STATUS: ${{ steps.coordinator.outputs.coordinator_status || \'1\' }}' in workflow
    assert "COORDINATOR_OUTCOME" not in workflow
    assert 'coordinator_status != 0' in workflow
