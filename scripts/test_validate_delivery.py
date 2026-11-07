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
        "pushed_slugs": [TARGET_REPO],
        "target_issue": TARGET_ISSUE,
        "target_run": TARGET_RUN,
    }
    delivery.update(overrides)
    return delivery


def test_accepts_code_delivery_with_one_open_nonempty_pr():
    assert validate_delivery(_delivery()) == "code"


def test_accepts_one_pr_for_each_pushed_repository_slug():
    second_repo = "bacluc-agent/remote-project"
    second_pr = {
        "url": f"https://github.com/{second_repo}/pull/34",
        "state": "OPEN",
        "body": f"Fixes {TARGET_ISSUE}\n\nValidated by {TARGET_RUN}.",
    }
    delivery = _delivery(
        pushed_slugs=[TARGET_REPO, second_repo],
        prs=[_delivery()["prs"][0], second_pr],
    )

    assert validate_delivery(delivery) == "code"


@pytest.mark.parametrize(
    "pushed_slugs, prs",
    [
        ([TARGET_REPO, "bacluc-agent/remote-project"], [_delivery()["prs"][0]]),
        ([TARGET_REPO], [_delivery()["prs"][0], _delivery()["prs"][0]]),
        ([TARGET_REPO], [{**_delivery()["prs"][0], "url": "https://github.com/bacluc-agent/unrelated/pull/1"}]),
    ],
)
def test_rejects_missing_extra_or_unrelated_repository_deliveries(pushed_slugs, prs):
    with pytest.raises(DeliveryError):
        validate_delivery(_delivery(pushed_slugs=pushed_slugs, prs=prs))


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
        pushed_slugs=[],
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


@pytest.mark.parametrize(
    "body",
    [
        "<!-- agent-progress -->\n### Management summary\nvalid report text\n### Design decisions\nvalid decision text\n### Open PR\nnone\n### Verification evidence\nnone\n### Action runs\n| Run | Model | What happened | Result | Failure reason |\nmalformed",
        "<!-- agent-progress -->\n### Management summary\nvalid report text\n### Design decisions\nvalid decision text\n### Open PR\nnone\n### Verification evidence\nnone\n### Action runs\n| Run | Model | What happened | Result | Failure reason |\n| --- | ----- | ------------- | ------ | -------------- |\n| https://github.com/bacluc-agent-org/agent-runner/actions/runs/999 | test | old report for another run | completed | none |",
    ],
)
def test_rejects_malformed_or_stale_progress(body):
    with pytest.raises(DeliveryError):
        validate_delivery(_delivery(changed=False, prs=[], pushed=False, branch="", progress={"body": body}))


def test_accepts_zero_step_early_completion_with_current_run_evidence():
    delivery = _delivery(
        changed=False,
        prs=[],
        pushed=False,
        pushed_slugs=[],
        branch="",
        coordinator={"outcome": "success", "fatal": "", "steps": 0},
    )
    assert validate_delivery(delivery) == "report"


def test_workflow_wires_fail_closed_validation_before_completion():
    workflow = Path(__file__).parents[1] / ".github/workflows/opencode.yml"
    body = workflow.read_text(encoding="utf-8")
    assert "scripts/validate_delivery.py" in body
    assert "DELIVERY_OUTCOME" in body
    assert 'DELIVERY_OUTCOME:-}" == "success"' in body
    assert 'deny_file="$GITHUB_WORKSPACE/scripts/model-deny-list.txt"' in body
    assert 'PUSH_OUTCOME: ${{ steps.push-work.outcome }}' in body
    assert 'gh api --paginate "repos/$ISSUE_REPOSITORY/issues/$ISSUE_NUMBER/comments"' in body
    assert 'PUSHED_SLUGS: ${{ steps.push-work.outputs.pushed_slugs }}' in body
    assert 'slugs="${PUSHED_SLUGS:-}"' in body
    assert "pushed_slugs_json=" in body
    assert "done < <(printf '%s\\n' \"$slugs\")" in body
    assert 'printf \'%s\\n\' "$GITHUB_REPOSITORY" "$slugs" | sort -u' not in body
    assert body.index('if grep -Eq "$deny_re" <<<"$model"') < body.index('if [[ "$model" == openai/*')
    assert "git add -A" not in body
    assert "for attempt in 1 2" in body
    assert "Delivery validation feedback from the previous attempt" in body


def test_setup_selects_dynamic_release_and_keeps_branch_override():
    action = Path(__file__).parents[1] / ".github/actions/setup-opencode/action.yml"
    body = action.read_text(encoding="utf-8")
    assert 'if [[ -n "${SOURCE_RELEASE:-}" ]]' in body
    assert "sort -V" in body
    assert 'if [[ -n "${SOURCE_BRANCH:-}" ]]' in body
    assert 'git clone --quiet --branch "$SOURCE_BRANCH"' in body
    assert body.index('if [[ -n "${SOURCE_BRANCH:-}" ]]') < body.index('if [[ -n "${SOURCE_RELEASE:-}" ]]')


def test_runtime_helpers_are_checked_out_at_workflow_revision():
    workflow = Path(__file__).parents[1] / ".github/workflows/opencode.yml"
    body = workflow.read_text(encoding="utf-8")
    checkout = body.index("repository: bacluc-agent-org/agent-runner")
    assert "ref: ${{ github.sha }}" in body[checkout : checkout + 300]
    assert 'python3 "$RUNNER_TEMP/agent-runner/scripts/validate_delivery.py"' in body
