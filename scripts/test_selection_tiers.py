import os
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

from selection_tiers import Candidate, is_standing, parse_branches, render_prompt, select_tier, tier

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/hourly-issue.yml"


def candidate(number=1, **values):
    defaults = dict(
        number=number,
        title="Issue",
        labels=[],
        created_at="2026-01-01T00:00:00Z",
        pr_state="none",
        pr_number=None,
        pr_last_commit_at=None,
        last_human_comment_at=None,
        attempts=0,
        recent_attempts=0,
        is_standing=False,
    )
    defaults.update(values)
    return Candidate(**defaults)


def test_branch_history_and_recent_repeat():
    branches = ["agent-run/330-37135909176", "issue-2-37135909177", "invalid"]
    assert parse_branches(branches) == [(2, 37135909177), (330, 37135909176)]
    assert not any(__import__("re").search(r"issue-[0-9]+", branch) for branch in branches[:1])
    assert tier(candidate(330, attempts=1)) == 2
    assert tier(candidate(9, attempts=3, recent_attempts=1)) == 3
    assert tier(candidate(76, title="Standing research", attempts=14, recent_attempts=1, is_standing=True)) == 3


def test_durable_attempt_label_counts_as_history_without_branch():
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from subprocess import run

    with TemporaryDirectory() as directory:
        candidates = Path(directory) / "candidates.json"
        branches = Path(directory) / "branches.txt"
        tail = Path(directory) / "tail.txt"
        candidates.write_text(json.dumps([{"number": 42, "labels": [{"name": "agent-attempted"}]}]))
        branches.write_text("")
        tail.write_text("Choose\n")
        result = run(["python3", "scripts/selection_tiers.py", "--candidates", str(candidates), "--branches", str(branches), "--tail-file", str(tail)], check=True, capture_output=True, text=True)
    assert "attempts for 42: 1" in result.stderr
    assert "Candidate tiers: 0=0 1=0 2=1 3=0" in result.stderr
    assert "[attempts: 1] [tier: 2]" in result.stdout


def test_tiers_cover_feedback_history_and_finished_work():
    assert tier(candidate(pr_state="open", pr_last_commit_at="2026-01-01T00:00:00Z", last_human_comment_at="2026-01-02T00:00:00Z")) == 0
    assert tier(candidate(pr_state="open")) == 3
    assert tier(candidate(pr_state="open", pr_last_commit_at="2026-01-02T00:00:00Z", last_human_comment_at="2026-01-01T00:00:00Z")) == 3
    assert tier(candidate(pr_state="open", pr_last_commit_at="2026-01-02T00:00:00Z", last_human_comment_at="2026-01-02T00:00:00Z")) == 3
    assert tier(candidate(pr_state="merged")) == 2
    assert tier(candidate(pr_state="closed")) == 2
    assert tier(candidate()) == 1
    assert is_standing("Standing project")
    assert is_standing("Some task", ["meta: standing"])


def test_select_best_available_and_render_only_that_tier():
    better, awaiting = candidate(2), candidate(3, pr_state="open")
    assert select_tier([awaiting, better]) == 1
    assert select_tier([awaiting]) == 3
    big = candidate(4)
    big.body = "x" * 3000
    big.labels = ["many", "labels"]
    assert tier(big) == tier(candidate(5))
    prompt = render_prompt([better, awaiting], 1, "Reply\nSELECTED_ISSUE: <number>")
    assert "2: Issue" in prompt and "3: Issue" not in prompt
    assert "[attempts: 0] [tier: 1]" in prompt
    assert "avoid" not in prompt.lower()
    assert prompt.rstrip().endswith("SELECTED_ISSUE: <number>")


def _enrichment_line():
    return next(
        line.strip()
        for line in WORKFLOW.read_text().splitlines()
        if line.strip().startswith("jq -nc --arg num") and "enrichment.jsonl" in line
    )


def _enrich(issue_ts, pr_ts):
    import json

    with TemporaryDirectory() as directory:
        script = (
            f"num=364; pr_info=none; issue_human_ts={issue_ts!r}; pr_human_ts={pr_ts!r}\n"
            f"RUNNER_TEMP={directory!r}\n"
            f"{_enrichment_line()}\n"
        )
        result = run(["bash", "-Eeuo", "pipefail", "-c", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads((Path(directory) / "enrichment.jsonl").read_text())["last_human_comment_at"]


def test_enrichment_counts_pr_review_feedback_and_never_yields_a_broken_timestamp():
    stamp = "2026-01-09T00:00:00Z"
    # PR review feedback counts even when the issue itself has none.
    assert _enrich("none", stamp) == stamp
    # Newer issue feedback still wins over older PR feedback.
    assert _enrich("2026-02-01T00:00:00Z", stamp) == "2026-02-01T00:00:00Z"
    # gh's empty-result sentinels and failed lookups degrade to JSON null, never a
    # non-timestamp string that would crash _newer()'s datetime.fromisoformat.
    for issue_ts, pr_ts in [("none", "none"), ("null", "none"), ("", ""), ("null", stamp)]:
        assert _enrich(issue_ts, pr_ts) == ("2026-01-09T00:00:00Z" if pr_ts == stamp else None)


def test_pr_lookup_variables_are_initialised_before_the_pr_feedback_lookup():
    content = WORKFLOW.read_text()
    assert content.index('pr_number=""') < content.index('gh pr view "$pr_number"')
    assert content.index('pr_repo=""') < content.index('gh pr view "$pr_number"')


def test_workflow_uses_latest_commit_and_created_at_when_pr_has_no_commits():
    content = WORKFLOW.read_text()
    assert "--json number,createdAt,updatedAt,headRefName,state,title" in content
    assert "sort -t'|' -k4 -r" in content
    assert 'gh api --paginate --slurp "repos/$pr_repo/pulls/$pr_number/commits" | jq -er --arg fallback "$pr_created"' in content
    assert '([.[][] | .commit.committer.date] | max) // $fallback' in content
    assert 'pr_last_commit_at:(' in content
    assert 'map(select(test("^[0-9]")))|max // null' in content


def test_selection_tail_keeps_contract_and_forbids_tools():
    tail = (Path(__file__).parents[1] / "scripts/issue-selection-tail.txt").read_text()
    assert "End with the exact final line: SELECTED_ISSUE: <number>" in tail
    assert "No text may follow that line." in tail
    assert "no tools, no lookups, no file reads" in tail
    assert "fields are precomputed" in tail
    assert "choosing the best-so-far candidate" in tail
    assert "tool calls" not in tail
    assert "gh lookup" not in tail
    assert "research" not in tail.lower()


def test_issue_selector_denies_every_tool():
    selector = (Path(__file__).parents[1] / ".opencode/agent/issue-selector.md").read_text()
    frontmatter = selector.split("---", 2)[1]
    assert '"*": deny' in frontmatter
    assert "read: allow" not in frontmatter
    assert "glob: allow" not in frontmatter
    assert "grep: allow" not in frontmatter
    assert "webfetch: allow" not in frontmatter
    assert "bash:" not in frontmatter
    assert "opencode debug" not in selector
    assert "ONE targeted lookup" not in selector
    assert "12 tool calls" not in selector
    assert "stamped the result into every candidate line" in selector
    assert "You have no tools" in selector


def test_selection_default_timeout_is_fifteen_minutes():
    workflow = WORKFLOW.read_text()
    assert "default: 15" in workflow
    assert "${{ inputs.selection_timeout_minutes || 15 }}" in workflow


def _pick_decision_block():
    text = WORKFLOW.read_text()
    return text[text.index("# pick-decision-start") : text.index("# pick-decision-end")]


def _run_pick_decision(tmp_path, selection_text, candidates_text="42\n", status=0, tee_status=0, fatal=False):
    (tmp_path / "selection.out").write_text(selection_text)
    (tmp_path / "candidate-numbers.txt").write_text(candidates_text)
    if fatal:
        (tmp_path / "opencode-fatal.json").write_text('{"reason": "balance", "model": "opencode/big-pickle"}')
    script = (
        'selection="$RUNNER_TEMP/selection.out"\n'
        'candidates="$RUNNER_TEMP/candidate-numbers.txt"\n'
        'stop_token="pick-decision-test-token"\n'
        f"selection_status={status}\n"
        f"selection_tee_status={tee_status}\n"
        "SELECTION_TIMEOUT_MINUTES=10\n"
        f"{_pick_decision_block()}\n"
        "printf 'REACHED issue=%s' \"$issue_number\"\n"
    )
    return run(["bash", "-Eeuo", "pipefail", "-c", script], env={**os.environ, "RUNNER_TEMP": str(tmp_path)}, capture_output=True, text=True)


class TestPickDecisionBlock:
    VALID = "Implementation prompt body\nSELECTED_ISSUE: 42\n"
    PROSE = "I meant to pick an issue but forgot the marker line\nrandom prose\n"

    def test_valid_pick_dispatches_despite_nonzero_opencode_status(self, tmp_path):
        result = _run_pick_decision(tmp_path, self.VALID, status=1)
        assert result.returncode == 0, result.stderr
        assert "REACHED issue=42" in result.stdout
        assert "::warning::opencode run exited 1; validating its output before dispatch" in result.stdout + result.stderr

    def test_fatal_marker_rejects_a_valid_pick(self, tmp_path):
        result = _run_pick_decision(tmp_path, self.VALID, fatal=True)
        assert result.returncode == 1
        assert "Fatal provider error recorded during selection; not dispatching." in result.stdout + result.stderr
        assert "REACHED" not in result.stdout

    def test_empty_output_is_rejected_before_the_fatal_marker(self, tmp_path):
        result = _run_pick_decision(tmp_path, "\n   \n", fatal=True)
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "Selection produced no output; not dispatching." in combined
        assert "Fatal provider error recorded" not in combined

    def test_prose_without_selected_issue_prints_output_tail_diagnostics(self, tmp_path):
        result = _run_pick_decision(tmp_path, self.PROSE)
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "missing or invalid SELECTED_ISSUE" in combined
        open_fence = combined.index("::stop-commands::pick-decision-test-token")
        tail_at = combined.index("random prose")
        close_fence = combined.index("::pick-decision-test-token::")
        assert open_fence < tail_at < close_fence

    def test_selected_issue_outside_the_candidates_is_rejected(self, tmp_path):
        result = _run_pick_decision(tmp_path, "picks\nSELECTED_ISSUE: 999\n")
        assert result.returncode == 1
        assert "missing or invalid SELECTED_ISSUE" in result.stdout + result.stderr

    def test_timeout_status_with_prose_emits_the_timeout_annotation(self, tmp_path):
        result = _run_pick_decision(tmp_path, self.PROSE, status=124)
        assert result.returncode == 1
        assert "::error::issue selection exceeded the 10m limit (opencode exit 124)" in result.stdout + result.stderr

    def test_tee_failure_exits_with_the_tee_status(self, tmp_path):
        result = _run_pick_decision(tmp_path, self.VALID, tee_status=3)
        assert result.returncode == 3

    def test_workflow_warns_instead_of_aborting_on_nonzero_opencode_exit(self):
        content = WORKFLOW.read_text()
        assert "::warning::opencode run exited %s; validating its output before dispatch" in content
        assert "Selection failed (opencode=%s); not dispatching." not in content


def _gh_retry_function():
    content = WORKFLOW.read_text()
    start = content.index("gh_retry() {")
    end = content.index("\n          }\n", start) + len("\n          }\n")
    return content[start:end]


def _run_gh_retry(tmp_path, succeed_at):
    calls = tmp_path / "gh-calls.txt"
    script = (
        f"calls_file={str(calls)!r}\n"
        f"SUCCEED_AT={succeed_at}\n"
        "gh() {\n"
        "  printf 'call\\n' >> \"$calls_file\"\n"
        "  n=$(($(wc -l < \"$calls_file\")))\n"
        "  if (( n < SUCCEED_AT )); then return 1; fi\n"
        "  printf 'payload-%s\\n' \"$n\"\n"
        "}\n"
        "sleep() { :; }\n"
        f"{_gh_retry_function()}\n"
        "gh_retry gh api test\n"
    )
    result = run(["bash", "-Eeuo", "pipefail", "-c", script], capture_output=True, text=True)
    return result, len(calls.read_text().splitlines()) if calls.exists() else 0


def test_workflow_retries_the_three_transient_gh_calls():
    content = WORKFLOW.read_text()
    assert content.count("gh_retry gh api") >= 3


def test_gh_retry_succeeds_on_the_third_attempt(tmp_path):
    result, calls = _run_gh_retry(tmp_path, succeed_at=3)
    assert result.returncode == 0, result.stderr
    assert "payload-3" in result.stdout
    assert calls == 3


def test_gh_retry_gives_up_after_exactly_three_attempts(tmp_path):
    result, calls = _run_gh_retry(tmp_path, succeed_at=10**9)
    assert result.returncode != 0
    assert calls == 3
