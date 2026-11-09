from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

from selection_tiers import Candidate, is_claimed, is_standing, parse_branches, render_prompt, select_tier, tier

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


def test_bare_and_run_scoped_claims_are_excluded_independently():
    import json

    with TemporaryDirectory() as directory:
        candidates = Path(directory) / "candidates.json"
        branches = Path(directory) / "branches.txt"
        tail = Path(directory) / "tail.txt"
        candidates.write_text(
            json.dumps(
                [
                    {"number": 1, "labels": [{"name": "agent-running"}]},
                    {"number": 2, "labels": [{"name": "agent-running:123"}]},
                    {"number": 3, "labels": []},
                ]
            )
        )
        branches.write_text("")
        tail.write_text("Choose\n")
        result = run(
            [
                "python3",
                "scripts/selection_tiers.py",
                "--candidates",
                str(candidates),
                "--branches",
                str(branches),
                "--tail-file",
                str(tail),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    assert is_claimed(["agent-running"])
    assert is_claimed(["agent-running:123"])
    assert not is_claimed(["agent-attempted"])
    assert "3: " in result.stdout
    assert "1: " not in result.stdout
    assert "2: " not in result.stdout


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
    assert '''pr_last_commit="$(gh api --paginate --slurp "repos/$pr_repo/pulls/$pr_number/commits" | jq -er --arg fallback "$pr_created" '([.[][] | .commit.committer.date] | max) // $fallback')" || pr_last_commit="$pr_created"''' in content
    assert '([.[][] | .commit.committer.date] | max) // $fallback' in content
    assert 'pr_last_commit_at:(' in content
    assert 'map(select(test("^[0-9]")))|max // null' in content


def test_workflow_claims_are_run_scoped_and_terminal_cleanup_is_conservative():
    content = WORKFLOW.read_text()
    assert 'claim_label="agent-running:${GITHUB_RUN_ID}"' in content
    assert 'gh run view "$claim_run_id" -R bacluc-agent-org/agent-runner' in content
    assert 'queued|in_progress|unknown) continue' in content
    assert 'gh run view "$claim_run_id" --json status' not in content
    assert 'if: always()' in content
    assert 'issues?state=all&labels=${claim_label}' in content
    assert '--remove-label "$claim_label"' in content


def test_candidate_query_excludes_bare_and_prefixed_claims_without_label_search_filters():
    content = WORKFLOW.read_text()
    query = 'search/issues?q=repo:${ISSUE_REPOSITORY}+is:open+is:issue+label:ready-for-implementation+sort:created-asc&per_page=50'
    assert query in content
    assert '-label:agent-running' not in content
    assert 'ascii_downcase != "agent-running"' in content
    assert 'startswith("agent-running:") | not' in content


def test_standing_pass_validates_repeated_evidence_and_is_idempotent():
    from standing_pass import RUN_URL, create_follow_up
    import standing_pass

    report = """FOLLOW_UP_TITLE: Fix repeated selector failure\nFOLLOW_UP_BODY_BEGIN\n## Goal\nFix the repeated selector failure.\n\n## How to implement\n1. Update the selector.\n\nEvidence: https://github.com/bacluc-agent-org/agent-runner/actions/runs/10 and https://github.com/bacluc-agent-org/agent-runner/actions/runs/11\nFOLLOW_UP_BODY_END\n"""
    calls = []

    def fake_gh(*args):
        calls.append(args)
        if args[0] == "api":
            return "<!-- standing-pass:42 -->"
        return "created"

    original = standing_pass.run_gh
    standing_pass.run_gh = fake_gh
    try:
        assert create_follow_up(report, "bacluc-agent/agent-todo", "42") == "already-created"
    finally:
        standing_pass.run_gh = original
    assert any(args[0] == "api" for args in calls)
    assert RUN_URL in report


def test_claim_cleanup_preserves_only_nonterminal_statuses():
    for status in ("queued", "in_progress", "unknown"):
        result = run(
            [
                "bash",
                "-c",
                'case "$1" in queued|in_progress|unknown) exit 0;; *) exit 1;; esac',
                "claim-status",
                status,
            ]
        )
        assert result.returncode == 0
    for status in ("completed", "failure", "cancelled", "timed_out", "action_required"):
        result = run(
            [
                "bash",
                "-c",
                'case "$1" in queued|in_progress|unknown) exit 0;; *) exit 1;; esac',
                "claim-status",
                status,
            ]
        )
        assert result.returncode != 0
