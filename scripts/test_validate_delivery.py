from pathlib import Path

import pytest

from validate_delivery import DeliveryError, validate_delivery

ISSUE = "https://github.com/bacluc-agent/agent-todo/issues/433"
RUN = "https://github.com/bacluc-agent-org/agent-runner/actions/runs/123"
REPO = "bacluc-agent-org/agent-runner"


def progress(run=RUN):
    return f"""<!-- agent-progress -->

### Management summary

Implemented delivery validation for {ISSUE}.

### Design decisions

Fail closed on incomplete delivery for {RUN}.

### Open PR

none - this run pushed no pull request

### Verification evidence

{RUN}

### Action runs

| Run | Model | What happened | Result | Failure reason |
| --- | ----- | ------------- | ------ | -------------- |
| {run} | test | validated delivery | pending |  |

<details><summary>Context</summary>
</details>"""


def delivery(**overrides):
    result = {
        "coordinator": {"outcome": "success", "fatal": ""},
        "summary": f"Implemented the fix for {ISSUE}; see {RUN}.",
        "target_issue": ISSUE,
        "target_run": RUN,
        "push_outcome": "success",
        "changed": True,
        "pushed": True,
        "branch": "agent-run/433-123",
        "pushed_slugs": [REPO],
        "prs": [{"url": f"https://github.com/{REPO}/pull/1", "state": "OPEN", "body": f"Fixes {ISSUE}; validated by {RUN}."}],
        "progress": {"body": progress()},
    }
    result.update(overrides)
    return result


def test_accepts_valid_code_delivery():
    assert validate_delivery(delivery()) == "code"


def test_rejects_exit_zero_without_delivery():
    with pytest.raises(DeliveryError):
        validate_delivery(delivery(summary="done", changed=False, pushed=False, branch="", prs=[]))


@pytest.mark.parametrize(
    "changes",
    [
        {"prs": []},
        {"prs": [{"url": "bad", "state": "OPEN", "body": "body"}]},
        {"progress": {"body": ""}},
        {"progress": {"body": progress("https://github.com/bacluc-agent-org/agent-runner/actions/runs/999")}},
        {"progress": {"body": progress().replace("### Action runs", "### Wrong")}},
    ],
)
def test_rejects_invalid_missing_or_stale_delivery(changes):
    with pytest.raises(DeliveryError):
        validate_delivery(delivery(**changes))


def test_accepts_valid_no_code_report():
    assert validate_delivery(delivery(changed=False, pushed=False, pushed_slugs=[], branch="", prs=[])) == "report"


def test_rejects_no_code_delivery_that_claims_a_push():
    with pytest.raises(DeliveryError):
        validate_delivery(delivery(changed=False, pushed=True, pushed_slugs=[REPO], branch="", prs=[]))


def test_workflow_orders_validation_after_push_and_before_completion_status():
    body = Path(".github/workflows/opencode.yml").read_text(encoding="utf-8")
    assert "scripts/validate_delivery.py" in body
    assert "for attempt in 1 2" in body
    assert "Delivery validation feedback from the previous attempt" in body
    assert body.index("id: push-work") < body.index("id: delivery-validation") < body.index("name: Post run-result comment")
    assert 'DELIVERY_OUTCOME:-}" == "success"' in body


def test_clean_coordinator_branch_preserves_repository_slug_for_validation():
    body = Path(".github/workflows/opencode.yml").read_text(encoding="utf-8")
    branch_detection = body.index('if [[ -z "$WORK_BRANCH"')
    no_changes_output = body.index("else\n            printf 'No changes to push.")
    assert body.index("PUSHED_SLUGS=", branch_detection, no_changes_output) < no_changes_output
    assert "echo \"pushed_slugs<<EOF\"" in body[no_changes_output:]


def test_clean_coordinator_branch_produces_a_valid_delivery_object():
    body = Path(".github/workflows/opencode.yml").read_text(encoding="utf-8")
    branch_detection = body.index('if [[ -z "$WORK_BRANCH"')
    no_changes_output = body.index("else\n            printf 'No changes to push.")
    clean_branch = body[branch_detection : body.index("printf 'No changes in", branch_detection)]
    assert "PUSHED_REPOS=" in clean_branch

    pushed_repos = "/workspace/repo (feature-branch)\n"
    assert validate_delivery(delivery(pushed=bool(pushed_repos), pushed_slugs=[REPO])) == "code"
