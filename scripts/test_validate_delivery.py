from pathlib import Path

import pytest

from validate_delivery import DeliveryError, validate_delivery

TARGET_REPO = "bacluc-agent-org/agent-runner"
TARGET_ISSUE = "https://github.com/bacluc-agent/agent-todo/issues/433"
TARGET_RUN = "https://github.com/bacluc-agent-org/agent-runner/actions/runs/123"


def _delivery(**overrides):
    delivery = {
        "coordinator": {"outcome": "success", "fatal": ""},
        "changed": True,
        "summary": f"Implemented the requested change; see {TARGET_ISSUE} and {TARGET_RUN}.",
        "prs": [
            {
                "url": "https://github.com/bacluc-agent-org/agent-runner/pull/12",
                "state": "OPEN",
                "body": f"Fixes {TARGET_ISSUE}\n\nImplemented the delivery guard. See {TARGET_RUN}.",
            }
        ],
        "progress": {"body": f"<!-- agent-progress -->\n\n### Management summary\nImplemented the guard; see {TARGET_ISSUE} and {TARGET_RUN}.\n\n### Design decisions\nFail closed.\n\n### Open PR\nnone - this run pushed no pull request\n\n### Verification evidence\n{TARGET_RUN}#job:1#step:1\n\n### Action runs\n\n| Run | Model | What happened | Result | Failure reason |\n| --- | --- | --- | --- | --- |\n| {TARGET_RUN} | test | tested | passed |  |"},
        "pushed": True,
        "branch": "agent-run/433-123",
        "push_outcome": "success",
        "target_repo": TARGET_REPO,
        "target_issue": TARGET_ISSUE,
        "target_run": TARGET_RUN,
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
        summary=f"Confirmed the requested behavior; see {TARGET_ISSUE} and {TARGET_RUN}.",
        prs=[],
        progress=_delivery()["progress"],
        pushed=False,
        branch="",
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
        {"changed": False, "branch": "agent-run/433-123"},
        {"push_outcome": "failure"},
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


def test_rejects_pr_from_another_repository():
    delivery = _delivery(prs=[{**_delivery()["prs"][0], "url": "https://github.com/bacluc-agent/other/pull/1"}])
    with pytest.raises(DeliveryError):
        validate_delivery(delivery)


def test_rejects_no_code_report_without_body():
    delivery = _delivery(changed=False, prs=[], pushed=False, branch="", progress={"valid": True, "report": True})
    with pytest.raises(DeliveryError):
        validate_delivery(delivery)


def test_workflow_wires_fail_closed_validation_before_completion():
    workflow = Path(__file__).parents[1] / ".github/workflows/opencode.yml"
    body = workflow.read_text(encoding="utf-8")
    assert "scripts/validate_delivery.py" in body
    assert "DELIVERY_OUTCOME" in body
    assert 'DELIVERY_OUTCOME:-}" == "success"' in body
    assert 'deny_file="$GITHUB_WORKSPACE/scripts/model-deny-list.txt"' in body
    assert 'PUSH_OUTCOME: ${{ steps.push-work.outcome }}' in body
    assert 'gh api --paginate "repos/$ISSUE_REPOSITORY/issues/$ISSUE_NUMBER/comments"' in body
    assert 'slugs="$GITHUB_REPOSITORY"' in body
    assert body.index('if grep -Eq "$deny_re" <<<"$model"') < body.index('if [[ "$model" == openai/*')


def test_setup_pins_default_release_and_keeps_branch_override():
    action = Path(__file__).parents[1] / ".github/actions/setup-opencode/action.yml"
    body = action.read_text(encoding="utf-8")
    assert "SOURCE_RELEASE=2.4" in body
    assert 'if [[ -n "${SOURCE_BRANCH:-}" ]]' in body
    assert 'git clone --quiet --branch "$SOURCE_BRANCH"' in body
