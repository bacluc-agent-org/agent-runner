import os
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
from textwrap import dedent

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


def test_clean_coordinator_branch_output_is_consumed_by_delivery_validation():
    body = Path(".github/workflows/opencode.yml").read_text(encoding="utf-8")
    step = body[body.index("      - name: Ensure work is pushed") : body.index("      - name: Validate delivery")]
    script = dedent(step[step.index("        run: |\n") + len("        run: |\n") :])

    with TemporaryDirectory() as directory:
        workspace = Path(directory) / "repo"
        workspace.mkdir()
        run(["git", "init", "-q", "-b", "feature-branch", str(workspace)], check=True)
        (workspace / "tracked.txt").write_text("clean\n", encoding="utf-8")
        run(["git", "-C", str(workspace), "add", "tracked.txt"], check=True)
        run(
            ["git", "-C", str(workspace), "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture"],
            check=True,
        )
        run(["git", "-C", str(workspace), "remote", "add", "origin", f"https://github.com/{REPO}.git"], check=True)
        output = Path(directory) / "github-output"
        result = run(
            ["bash", "-Eeuo", "pipefail", "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_WORKSPACE": str(workspace),
                "GITHUB_TOKEN": "test-token",
                "ISSUE_NUMBER": "433",
            },
        )
        assert "No changes in" in result.stdout

        values = {}
        multiline = None
        for line in output.read_text(encoding="utf-8").splitlines():
            if line.endswith("<<EOF"):
                multiline = line[:-5]
                values[multiline] = []
            elif line == "EOF":
                multiline = None
            elif multiline is not None:
                values[multiline].append(line)
            else:
                key, value = line.split("=", 1)
                values[key] = value

    pushed_repos = "\n".join(values["pushed_repos"])
    pushed_slugs = values["pushed_slugs"]
    branch = values["branch_name"]
    assert pushed_repos.endswith("/repo (feature-branch)")
    assert pushed_slugs == [REPO]
    assert branch == "feature-branch"
    assert validate_delivery(
        delivery(pushed=bool(pushed_repos), branch=branch, pushed_slugs=pushed_slugs)
    ) == "code"
