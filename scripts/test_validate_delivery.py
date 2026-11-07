from pathlib import Path

import pytest

from validate_delivery import DeliveryError, validate_delivery


def _delivery(**overrides):
    delivery = {
        "coordinator": {"outcome": "success", "fatal": ""},
        "changed": True,
        "summary": "Implemented the requested change and added regression coverage.",
        "prs": [
            {
                "url": "https://github.com/bacluc-agent-org/agent-runner/pull/12",
                "state": "OPEN",
                "body": "Fixes https://github.com/bacluc-agent/agent-todo/issues/433\n\nImplemented the delivery guard.",
            }
        ],
        "progress": {"valid": True},
        "pushed": True,
    }
    delivery.update(overrides)
    return delivery


def test_accepts_code_delivery_with_one_open_nonempty_pr():
    assert validate_delivery(_delivery()) == "code"


def test_accepts_no_code_delivery_with_canonical_progress_report():
    body = """<!-- agent-progress -->

### Management summary

Confirmed the requested behavior is already present and reported the result.

### Design decisions

The existing behavior is sufficient.

### Open PR

none - this run pushed no pull request

### Verification evidence

none

### Action runs

| Run | Model | What happened | Result | Failure reason |
| --- | ----- | ------------- | ------ | -------------- |
"""
    delivery = _delivery(
        changed=False,
        summary="Confirmed the requested behavior is already present and reported the result.",
        prs=[],
        progress={"body": body},
        pushed=False,
    )

    assert validate_delivery(delivery) == "report"


@pytest.mark.parametrize(
    "changes",
    [
        {"coordinator": {"outcome": "failure", "fatal": ""}},
        {"coordinator": {"outcome": "success", "fatal": "provider"}},
        {"changed": True, "prs": []},
        {"changed": True, "prs": [_delivery()["prs"][0], _delivery()["prs"][0]]},
        {"changed": True, "prs": [{"url": "https://github.com/x/y/pull/1", "state": "OPEN", "body": ""}]},
        {"changed": True, "pushed": False},
        {"changed": False, "prs": [_delivery()["prs"][0]]},
        {"changed": False, "progress": {"body": "<!-- agent-progress -->\n### Management summary\nDone"}},
    ],
)
def test_rejects_incomplete_or_ambiguous_delivery(changes):
    delivery = _delivery()
    delivery.update(changes)
    with pytest.raises(DeliveryError):
        validate_delivery(delivery)


def test_rejects_placeholder_summary():
    delivery = _delivery(summary="Task complete")
    with pytest.raises(DeliveryError):
        validate_delivery(delivery)


def test_workflow_wires_fail_closed_validation_before_completion():
    workflow = Path(__file__).parents[1] / ".github/workflows/opencode.yml"
    body = workflow.read_text(encoding="utf-8")
    assert "scripts/validate_delivery.py" in body
    assert "DELIVERY_OUTCOME" in body
    assert 'DELIVERY_OUTCOME:-}" == "success"' in body


def test_setup_pins_default_release_and_keeps_branch_override():
    action = Path(__file__).parents[1] / ".github/actions/setup-opencode/action.yml"
    body = action.read_text(encoding="utf-8")
    assert "SOURCE_RELEASE=2.4" in body
    assert 'if [[ -n "${SOURCE_BRANCH:-}" ]]' in body
    assert 'git clone --quiet --branch "$SOURCE_BRANCH"' in body
