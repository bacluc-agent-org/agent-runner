"""Guard the review-fixes opt-in for tolerating failed PR legs (agent-todo#297)."""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
OPENCODE = (WORKFLOWS / "opencode.yml").read_text()
REVIEW_FIXES = (WORKFLOWS / "review-fixes.yml").read_text()
JOB_LEVEL = "    continue-on-error: ${{ inputs.continue_on_error == true }}"


def _between(text, start, stop):
    return text[text.index(start) : text.index(stop, text.index(start))]


def test_callee_declares_opt_in_boolean_input():
    inputs = _between(OPENCODE, "  workflow_call:", "\npermissions:")
    assert OPENCODE.count("      continue_on_error:") == 1
    assert re.search(
        r"      continue_on_error:\n"
        r"        description: .+\n"
        r"        required: false\n"
        r"        type: boolean\n"
        r"        default: false\n",
        inputs,
    )


def test_callee_tolerates_failure_only_when_input_is_true():
    assert OPENCODE.count(JOB_LEVEL) == 1
    assert JOB_LEVEL in OPENCODE[OPENCODE.index("\n  run:\n") :]
    assert not re.search(
        r"continue-on-error:\s*\$\{\{\s*inputs\.continue_on_error\s*\}\}", OPENCODE
    )


def test_review_fixes_opts_in_without_caller_continue_on_error():
    process_prs = _between(REVIEW_FIXES, "  process-prs:", "\n    secrets: inherit")
    assert "      fail-fast: false" in REVIEW_FIXES
    assert REVIEW_FIXES.count("      continue_on_error: true") == 1
    assert process_prs.count("      continue_on_error: true") == 1
    assert "continue-on-error" not in REVIEW_FIXES


def test_other_callers_stay_non_opt_in_and_no_caller_sets_continue_on_error():
    for name in (
        "review-fixes.yml",
        "hourly-issue.yml",
        "kanban-manager.yml",
        "standing-issue-manager.yml",
    ):
        text = (WORKFLOWS / name).read_text()
        assert not re.search(r"^    continue-on-error:", text, re.M), name
        if name != "review-fixes.yml":
            assert "continue_on_error" not in text, name
