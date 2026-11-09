import json
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory
import time

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
    assert 'bounded_enrichment_call "commit lookup for candidate #$num" gh api --paginate --slurp "repos/$pr_repo/pulls/$pr_number/commits"' in content
    assert '''pr_last_commit="$(jq -er --arg fallback "$pr_created" '([.[][] | .commit.committer.date] | max) // $fallback' "$commits_json")"''' in content
    assert '([.[][] | .commit.committer.date] | max) // $fallback' in content
    assert 'pr_last_commit_at:(' in content
    assert 'map(select(test("^[0-9]")))|max // null' in content


def test_workflow_bounds_and_reports_enrichment_without_changing_candidate_join():
    content = WORKFLOW.read_text()
    assert 'enrichment_budget="${ENRICHMENT_BUDGET_SECONDS:-1200}"' in content
    assert "enrichment_deadline=$((enrichment_started + enrichment_budget))" in content
    assert "timeout --signal=TERM --kill-after=2s \"${remaining}s\" \"$@\"" in content
    assert "Enrichment %s started" in content
    assert "Enrichment %s completed" in content
    assert "::warning::enrichment timeout during %s (forced termination after grace period)" in content
    assert "::warning::enrichment lookup failed during %s" in content
    assert "processed=%s skipped=%s" in content
    assert "enrichment finished in degraded status" in content
    assert content.index("enrichment_started=") < content.index("branch lookup")
    assert content.index("bounded_enrichment_call 'branch lookup'") < content.index("repository validation")
    assert content.index("PR listing") < content.index("Enriching candidate #%s started")


def test_partial_enrichment_join_preserves_unenriched_candidates():
    import json

    join_line = next(
        line.strip()
        for line in WORKFLOW.read_text().splitlines()
        if line.strip().startswith("jq -n --slurpfile issues")
    )
    with TemporaryDirectory() as directory:
        directory = Path(directory)
        (directory / "candidates.json").write_text(json.dumps([{"number": 1}, {"number": 2}]))
        (directory / "enrichment.jsonl").write_text('{"number":1,"pr_info":"none","last_human_comment_at":null}\n')
        result = run(
            [
                "bash",
                "-Eeuo",
                "pipefail",
                "-c",
                f'RUNNER_TEMP={str(directory)!r}; enriched="$RUNNER_TEMP/candidates.json"; {join_line}',
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert json.loads((directory / "candidates.final.json").read_text()) == [
            {"number": 1, "last_human_comment_at": None, "pr_state": "none", "pr_number": None, "pr_last_commit_at": None},
            {"number": 2},
        ]


def test_ignored_term_enrichment_is_force_killed_and_keeps_valid_partial_json():
    with TemporaryDirectory() as directory:
        directory = Path(directory)
        sleeper = directory / "ignore-term-and-sleep"
        sleeper.write_text("#!/usr/bin/env python3\nimport signal\nimport time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n")
        sleeper.chmod(sleeper.stat().st_mode | 0o111)
        script = r'''
set -Eeuo pipefail
budget=1
started=$SECONDS
deadline=$((started + budget))
degraded=0
remaining() {
  local value=$((deadline - SECONDS))
  (( value > 0 )) && printf '%s\n' "$value" || return 1
}
bounded() {
  local remaining_seconds status
  if ! remaining_seconds=$(remaining); then
    printf '::warning::enrichment budget exhausted before candidate\n' >&2
    degraded=1
    return 125
  fi
  set +e
  timeout --signal=TERM --kill-after=2s "${remaining_seconds}s" "$@"
  status=$?
  set -e
  if (( status == 124 || status == 137 )); then
    printf '::warning::enrichment timeout during candidate (forced termination after grace period)\n' >&2
    degraded=1
  elif (( status != 0 )); then
    degraded=1
  fi
  return "$status"
}
issues='[{"number":1},{"number":2}]'
enrichment="$RUNNER_TEMP/enrichment.jsonl"
: > "$enrichment"
for number in 1 2; do
  if bounded "$1" "$number"; then
    printf '{"number":%s,"pr_info":"enriched"}\n' "$number" >> "$enrichment"
  fi
done
jq -n --argjson issues "$issues" --slurpfile enrichment "$enrichment" \
  '$issues | map(. as $issue | ($enrichment | map(select(.number == $issue.number)) | .[0]) as $entry | if $entry then . + $entry else . end)'
printf 'degraded=%s\n' "$degraded" >&2
'''
        started = time.monotonic()
        result = run(
            ["bash", "-Eeuo", "pipefail", "-c", script, "test", str(sleeper)],
            env={"RUNNER_TEMP": str(directory)},
            capture_output=True,
            text=True,
        )
        assert time.monotonic() - started < 6
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == [{"number": 1}, {"number": 2}]
        assert "forced termination after grace period" in result.stderr
        assert "budget exhausted before candidate" in result.stderr
        assert "degraded=1" in result.stderr
