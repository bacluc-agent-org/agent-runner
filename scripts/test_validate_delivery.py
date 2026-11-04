import json

import pytest

from validate_delivery import validate_delivery

RUN = "https://github.com/org/runner/actions/runs/123"
ISSUE = "https://github.com/org/todo/issues/42"
PR = "https://github.com/org/repo/pull/5"
SUMMARY = "Investigated the reported issue and identified the underlying provider authentication failure."


def output(kind="report", summary=SUMMARY, url=""):
    return 'AGENT_OUTCOME: ' + json.dumps({"status": "completed", "kind": kind, "summary": summary, "url": url})


def fetch(path):
    assert path == "repos/org/repo/pulls/5"
    return {"state": "open", "changed_files": 2, "body": f"Fixes {ISSUE}"}


def test_code_requires_matching_nonempty_real_pr():
    assert validate_delivery(output("code", url=PR), ISSUE, RUN, "", False, fetch) == PR
    for response in ({"state": "closed", "changed_files": 2, "body": ISSUE}, {"state": "open", "changed_files": 0, "body": ISSUE}, {"state": "open", "changed_files": 2, "body": "unrelated"}):
        with pytest.raises(ValueError):
            validate_delivery(output("code", url=PR), ISSUE, RUN, "", False, lambda _: response)


def test_report_requires_current_canonical_issue_summary_or_run_output():
    comment = f"### Management summary\n\n{SUMMARY}\n\n{RUN}\n\n### Design decisions\n"
    assert validate_delivery(output(), ISSUE, RUN, comment, False, fetch) == ""
    assert validate_delivery(output(), "", RUN, "", False, fetch) == ""
    for text in ("", f"### Management summary\n{SUMMARY}\n### Design decisions\n{RUN}"):
        with pytest.raises(ValueError):
            validate_delivery(output(), ISSUE, RUN, text, False, fetch)


def test_incomplete_placeholder_duplicate_and_branch_only_fail():
    for text in ("", output(summary="done"), output().replace('"completed"', '"blocked"'), output() + "\n" + output(), output("code", url="https://github.com/org/repo/tree/branch")):
        with pytest.raises(ValueError):
            validate_delivery(text, ISSUE, RUN, "", False, fetch)
    with pytest.raises(ValueError):
        validate_delivery(output(), "", RUN, "", True, fetch)


def test_workflow_rejects_fatal_and_recovery_failures_before_delivery():
    from pathlib import Path

    workflow = (Path(__file__).parents[1] / ".github/workflows/opencode.yml").read_text()
    gate = workflow[workflow.index("      - name: Validate delivery"):workflow.index("      - name: Emit loop-termination metrics")]
    assert '"$COORDINATOR_OUTCOME" != success' in gate
    assert '"$PUSH_OUTCOME" == failure' in gate
    assert '-f "$RUNNER_TEMP/opencode-fatal.json"' in gate
    assert "steps.delivery.outcome" in workflow
    assert "ref: ${{ github.repository == 'bacluc-agent-org/agent-runner' && github.sha || '' }}" in workflow
