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


def test_claimed_candidates_are_excluded_bare_and_run_scoped():
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from subprocess import run

    assert is_claimed(["agent-running"])
    assert is_claimed(["agent-running:42745795778"])
    assert is_claimed(["Agent-Running:1:2"])
    assert is_claimed(["AGENT-RUNNING:1:2"])
    assert not is_claimed(["agent-attempted"])
    assert not is_claimed([])
    assert not is_claimed(None)
    with TemporaryDirectory() as directory:
        candidates = Path(directory) / "candidates.json"
        branches = Path(directory) / "branches.txt"
        tail = Path(directory) / "tail.txt"
        candidates.write_text(json.dumps([
            {"number": 3, "title": "Issue", "labels": [{"name": "agent-running"}]},
            {"number": 4, "title": "Issue", "labels": [{"name": "agent-running:42745795778"}]},
            {"number": 5, "title": "Issue", "labels": []},
        ]))
        branches.write_text("")
        tail.write_text("Choose\n")
        result = run(["python3", "scripts/selection_tiers.py", "--candidates", str(candidates), "--branches", str(branches), "--tail-file", str(tail)], check=True, capture_output=True, text=True)
    assert "3: Issue" not in result.stdout
    assert "4: Issue" not in result.stdout
    assert "5: Issue" in result.stdout


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


def _sweep_filter():
    lines = WORKFLOW.read_text().splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if 'issues?state=open&per_page=100" --jq' in line
    )
    parts = [lines[start].split("--jq '", 1)[1]]
    for line in lines[start + 1:]:
        if '\' > "$sweep_file"' in line:
            parts.append(line.split('\' > "$sweep_file"', 1)[0])
            return "\n".join(parts)
        parts.append(line)
    raise AssertionError("sweep jq filter not found")


def test_sweep_jq_selector_covers_bare_and_run_scoped_claims():
    import json
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from subprocess import run

    issues = [
        {"number": 1, "labels": [{"name": "agent-running"}], "pull_request": None},
        {"number": 2, "labels": [{"name": "agent-running:42750217969"}], "pull_request": None},
        {"number": 3, "labels": [{"name": "agent-attempted"}], "pull_request": None},
        {"number": 4, "labels": [{"name": "agent-running"}, {"name": "agent-running:1"}], "pull_request": None},
        {"number": 5, "labels": [{"name": "agent-running:2"}], "pull_request": {"url": "https://example.test"}},
        {"number": 6, "labels": [{"name": "Agent-Running:42750217969"}], "pull_request": None},
        {"number": 7, "labels": [{"name": "agent-running:1:2"}], "pull_request": None},
        {"number": 8, "labels": [{"name": "AGENT-RUNNING"}], "pull_request": None},
    ]
    with TemporaryDirectory() as directory:
        path = Path(directory) / "issues.json"
        path.write_text(json.dumps(issues))
        result = run(["jq", "-r", _sweep_filter(), str(path)], check=True, capture_output=True, text=True)
    assert result.stdout == (
        "1\tagent-running\n"
        "2\tagent-running:42750217969\n"
        "4\tagent-running,agent-running:1\n"
        "6\tAgent-Running:42750217969\n"
        "7\tagent-running:1:2\n"
        "8\tAGENT-RUNNING\n"
    )


def _sweep_status_block():
    lines = WORKFLOW.read_text().splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip().startswith('run_id="${claim_key#agent-running:}"')
    )
    for index in range(start, len(lines)):
        if lines[index].strip() == "esac":
            return "\n".join(lines[start:index + 1])
    raise AssertionError("sweep status block not found")


def test_sweep_keeps_live_or_unverifiable_claims_and_expires_terminal_or_missing_runs():
    block = _sweep_status_block()
    warning_502 = "::warning::could not look up run 123 for claim agent-running:123 on #7; keeping the claim"
    cases = [
        ("123", "queued", 0, "", False, None),
        ("123", "in_progress", 0, "", False, None),
        ("123", "completed", 0, "", True, None),
        ("123", "", 1, "gh: Not Found (HTTP 404)", True, None),
        ("123", "", 1, "gh: Server Error (HTTP 502)", False, warning_502),
        ("123", "", 1, "", False, warning_502),
        ("123", "", 0, "", False, warning_502),
        ("", "", 0, "", True, None),
    ]
    for run_id, status, returncode, stderr, swept, warning in cases:
        with TemporaryDirectory() as directory:
            claim = f"agent-running:{run_id}"
            script = f"""set -Eeuo pipefail
GITHUB_REPOSITORY=bacluc-agent-org/agent-runner
ISSUE_REPOSITORY=bacluc-agent/agent-todo
RUNNER_TEMP={directory!r}
claim={claim!r}
claim_key={claim!r}
num=7
deleted="$RUNNER_TEMP/deleted.txt"
lookups="$RUNNER_TEMP/lookups.txt"
gh() {{
  if [[ "$*" == *' -X DELETE '* ]]; then printf '%s\\n' "${{@: -1}}" >> "$deleted"; return 0; fi
  printf 'lookup\\n' >> "$lookups"
  printf '%s\\n' {stderr!r} >&2
  printf '%s' {status!r}
  return {returncode}
}}
{block}
"""
            result = run(["bash", "-Eeuo", "pipefail", "-c", script], capture_output=True, text=True)
            assert result.returncode == 0, result.stderr
            assert (Path(directory) / "deleted.txt").exists() == swept, result.stdout
            lookups = Path(directory) / "lookups.txt"
            assert lookups.exists() == bool(run_id), result.stdout
            if warning:
                assert warning in result.stdout


def test_workflow_claims_are_run_scoped_swept_and_always_released():
    content = WORKFLOW.read_text()
    assert 'claim_label="agent-running:${GITHUB_RUN_ID}"' in content
    assert 'gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}" --jq .status 2>"$status_err"' in content
    assert "HTTP 404" in content
    assert "not-found" in content
    assert "keeping the claim" in content
    assert "treating the claim as stale" not in content
    assert "run-view-error" not in content
    assert "status='unknown'" not in content
    assert 'queued|in_progress)' in content
    assert 'queued|in_progress|unknown)' not in content
    assert "concurrency:" in content
    assert "group: hourly-issue-runner" in content
    assert "cancel-in-progress: false" in content
    assert 'claim_key="${claim,,}"' in content
    assert 'run_id="${claim_key#agent-running:}"' in content
    assert 'done < "$sweep_file"' in content
    assert 'done < <(gh api --paginate' not in content
    assert 'refusing to select' in content
    assert 'select(ascii_downcase | . == "agent-running" or startswith("agent-running:"))' in content
    assert '[.labels[]?.name | ascii_downcase] | any(' in content
    assert 'gh api -X POST "repos/${ISSUE_REPOSITORY}/issues/${issue_number}/labels"' in content
    assert content.index('if [[ "$claim_key" == "agent-running" ]]') < content.index('run_id="${claim_key#agent-running:}"')
    assert 'Swept legacy claim %s from #%s' in content
    assert '::error::could not claim issue %s; not dispatching' in content
    assert 'claim_failed=true' in content
    assert "steps.pick.outputs.claim_failed != 'true'" in content
    assert "-label:agent-running:*" not in content
    assert "if: always() && needs.select.outputs.issue_number != ''" not in content
    release = content[content.index("  release:"):]
    assert "if: always()" in release
    assert "labels=${claim_label}" in release
    assert 'gh label delete "$claim_label" -R "$ISSUE_REPOSITORY" --yes' in release
    assert "issue_number" not in release
