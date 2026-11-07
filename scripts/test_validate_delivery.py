import pytest
from pathlib import Path

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
        "progress": {"marker": True, "valid": True},
        "pushed": True,
    }
    delivery.update(overrides)
    return delivery


def test_accepts_code_delivery_with_one_open_nonempty_pr():
    assert validate_delivery(_delivery()) == "code"


def test_accepts_no_code_delivery_with_canonical_progress_report():
    delivery = _delivery(
        changed=False,
        summary="Confirmed the requested behavior is already present and reported the result.",
        prs=[],
        progress={"marker": True, "valid": True, "report": True},
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
        {"changed": False, "progress": {"marker": True, "valid": False, "report": True}},
        {"changed": False, "progress": {"marker": True, "valid": True, "report": False}},
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
