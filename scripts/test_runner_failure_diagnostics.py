from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_opencode_workflow_reports_safe_auth_and_dispatch_context():
    workflow = (ROOT / ".github/workflows/opencode.yml").read_text()

    assert "auth_status='valid'" in workflow
    assert "auth_status='invalid'" in workflow
    assert "auth_status='missing'" in workflow
    assert "OPENCODE_AUTH_CONTENT present=%s" in workflow
    assert "Coordinator dispatch failed: model=%s provider=%s exit_status=%s auth_validation=%s" in workflow
    assert "Credential values are not logged." in workflow


def test_selector_diagnostics_distinguish_missing_and_out_of_scope_selection():
    workflow = (ROOT / ".github/workflows/hourly-issue.yml").read_text()

    assert "emitted no valid SELECTED_ISSUE:<number> line" in workflow
    assert "chose issue %s outside the candidate set (%s candidates)" in workflow
