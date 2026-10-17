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


def test_selection_tail_keeps_contract_and_research_budget():
    tail = (Path(__file__).parents[1] / "scripts/issue-selection-tail.txt").read_text()
    assert "End with the exact final line: SELECTED_ISSUE: <number>" in tail
    assert "No text may follow that line." in tail
    assert "read each candidate at most once" in tail
    assert "stop investigating, even if undecided" in tail
    assert "choosing the best-so-far candidate" in tail
    assert 'must not be cut by this budget' in tail
    assert 'note the doubt inside the implementation prompt' in tail


def _pr_list_jq_filter():
    for line in WORKFLOW.read_text().splitlines():
        if "--jq" in line and ".headRefName" in line:
            import re

            m = re.search(r"--jq '([^']+)'", line)
            assert m, line
            return m.group(1)
    raise AssertionError("pr list jq filter not found")


def _pr_lookup_grep_line():
    for line in WORKFLOW.read_text().splitlines():
        if 'match="$(grep -m1 -E' in line:
            return line.strip()
    raise AssertionError("grep match line not found")


def _match_for(prs, num, issue_repo="bacluc-agent/agent-todo"):
    import json
    import shlex

    jq_filter = _pr_list_jq_filter()
    with TemporaryDirectory() as d:
        d = Path(d)
        pr_json = d / "prs.json"
        pr_json.write_text(json.dumps(prs))
        jq = run(["jq", "-r", jq_filter], stdin=pr_json.open(), capture_output=True, text=True)
        assert jq.returncode == 0, jq.stderr
        lookup_dir = d / "pr-lookup"
        lookup_dir.mkdir()
        key = issue_repo.replace("/", "_")
        (lookup_dir / f"{key}.lookup").write_text(jq.stdout)
        grep_line = _pr_lookup_grep_line()
        script = (
            f"num={num}\n"
            f"ISSUE_REPOSITORY={shlex.quote(issue_repo)}\n"
            f"pr_lookup_dir={shlex.quote(str(lookup_dir))}\n"
            f"key={shlex.quote(key)}\n"
            f"{grep_line}\n"
            "printf '%s\\n' \"$match\"\n"
        )
        result = run(["bash", "-Eeuo", "pipefail", "-c", script], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()


def _pr(number, body, title="Unrelated", head="feature/no-marker"):
    return {
        "number": number,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
        "headRefName": head,
        "state": "OPEN",
        "title": title,
        "body": body,
    }


def test_pr_body_reference_matches_head_ref_and_title_independent():
    pr_marker = _pr(101, "Related to bacluc-agent/agent-todo#364")
    assert _match_for([pr_marker], 364).split("|")[1] == "#101"


def test_pr_body_url_reference_matches():
    pr_url = _pr(105, "See https://github.com/bacluc-agent/agent-todo/issues/364")
    assert _match_for([pr_url], 364).split("|")[1] == "#105"


def test_pr_body_with_newlines_and_pipes_stays_single_line():
    pr_pipe = _pr(102, "Notes:\nfoo|bar\nissue-364\nbaz")
    match = _match_for([pr_pipe], 364)
    assert match.split("|")[1] == "#102"
    import json

    with TemporaryDirectory() as d:
        p = Path(d) / "prs.json"
        p.write_text(json.dumps([pr_pipe]))
        out = run(["jq", "-r", _pr_list_jq_filter()], stdin=p.open(), capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert out.stdout.count("\n") == 1


def test_pr_body_reference_negative_cases():
    assert _match_for([_pr(103, "Closes bacluc-agent/agent-todo#999")], 364) == ""
    assert _match_for([_pr(104, "Fixes #364")], 364) == ""
    assert _match_for([_pr(106, "See https://github.com/bacluc-agent/agent-todo/issues/3645")], 364) == ""
