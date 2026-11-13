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
    assert candidate(330, attempts=1).attempts == 1
    assert candidate(9, attempts=3, recent_attempts=1).recent_attempts == 1
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


def test_render_prompt_randomizes_all_candidates(monkeypatch):
    better, awaiting = candidate(2), candidate(3)
    big = candidate(4)
    big.body = "x" * 3000
    big.labels = ["many", "labels"]
    import random

    monkeypatch.setattr(random, "shuffle", lambda items: items.reverse())
    prompt = render_prompt([better, awaiting], 1, "Reply\nSELECTED_ISSUE: <number>")
    assert prompt.index("3: Issue") < prompt.index("2: Issue")
    assert "[attempts: 0] [tier: 1]" in prompt
    assert "avoid" not in prompt.lower()
    assert prompt.rstrip().endswith("SELECTED_ISSUE: <number>")


def test_select_best_available_and_render_only_that_tier():
    better, awaiting = candidate(2), candidate(3, pr_state="open")
    assert select_tier([awaiting, better]) == 1
    assert select_tier([awaiting]) == 3
    big = candidate(4)
    big.body = "x" * 3000
    big.labels = ["many", "labels"]
    assert tier(big) == tier(candidate(5))


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
    assert _enrich("none", stamp) == stamp
    assert _enrich("2026-02-01T00:00:00Z", stamp) == "2026-02-01T00:00:00Z"
    for issue_ts, pr_ts in [("none", "none"), ("null", "none"), ("", ""), ("null", stamp)]:
        assert _enrich(issue_ts, pr_ts) == (stamp if pr_ts == stamp else None)


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


def test_workflow_fetches_all_search_pages_and_passes_all_tiers():
    workflow = WORKFLOW.read_text()
    assert 'gh api "search/issues?' in workflow
    assert "--jq '.items' > \"$search_json\"" in workflow
    assert "&per_page=50\"" in workflow
    assert "sort:created-asc" in workflow
    tail = (Path(__file__).parents[1] / "scripts/issue-selection-tail.txt").read_text()
    assert "Candidate order is random" in tail
    assert "already filtered to one tier" in tail


def test_hourly_workflow_enriches_candidates_before_selection():
    content = WORKFLOW.read_text()
    assert 'gh api "repos/${GITHUB_REPOSITORY}/branches?per_page=100"' in content
    assert 'gh pr list -R "$repo"' in content
    assert 'gh issue view "$num"' in content


def test_hourly_selection_enriches_candidate_metadata_before_selection():
    content = WORKFLOW.read_text()
    assert "gh pr list -R" in content
    assert 'gh issue view "$num"' in content
    assert 'gh pr view "$pr_number"' in content
    assert 'gh api --paginate --slurp "repos/$pr_repo/pulls/$pr_number/commits"' in content
    assert 'gh issue edit "$issue_number" -R "$ISSUE_REPOSITORY" --add-label agent-running,agent-attempted' in content
    selector = (Path(__file__).parent / "selection_tiers.py").read_text()
    assert "issue_runs = [run for candidate, run in runs if candidate == number]" in selector
    assert "[PR:" in selector
    assert "[last-human-feedback:" in selector


def test_selector_retries_invocation_failures_on_distinct_eligible_models_only():
    content = WORKFLOW.read_text()
    retry = content[content.index('selection="$RUNNER_TEMP/selection.out"'):content.index("if ! grep -q '[^[:space:]]' \"$selection\"")]
    assert "selection_status != 0" in retry
    assert "not dispatching" in retry


def test_issue_selection_and_refinement_can_choose_verified_openai_oauth_models():
    root = Path(__file__).parents[1]
    for workflow_name, empty_message in (
        ("hourly-issue.yml", "No issue-selection model is available"),
        ("refine-issues.yml", "No refinement model is available"),
    ):
        content = (root / ".github/workflows" / workflow_name).read_text()
        assert "OPENCODE_AUTH_CONTENT: ${{ secrets.OPENCODE_AUTH_JSON }}" in content
        assert "mapfile -t free_models" in content
        assert '"${free_models[@]}"' in content
        assert empty_message in content
        if workflow_name == "refine-issues.yml":
            assert "timeout 8m opencode run --agent issue-refiner" in content

    opencode = (root / ".github/workflows/opencode.yml").read_text()
    assert "OPENCODE_AUTH_CONTENT: ${{ secrets.OPENCODE_AUTH_JSON }}" in opencode
    assert "mapfile -t openai_models < <(grep -E '^openai/'" in opencode
    assert '"${openai_models[@]}"' in opencode
    assert "openai/*)" in opencode
    assert "--agent model-discovery" in opencode
    assert 'configure_openai_model "$fallback_model"' in opencode
    assert 'configure_openai_model "$model"' in opencode
    assert 'if [[ "$fallback_model" == openai/* ]]; then' in opencode
    assert 'opencode run "${discovery_standalone_args[@]}" --agent model-discovery' in opencode
    assert 'if [[ "$MODEL" == openai/* ]]; then' in opencode
    assert 'opencode run "${coordinator_standalone_args[@]}" --agent "$AGENT"' in opencode
    assert "export OPENCODE_DB=" not in opencode
    assert 'unset OPENAI_ACCESS_TOKEN OPENAI_REFRESH_TOKEN OPENCODE_AUTH_CONTENT' in opencode
    assert 'baseURL:$baseURL,apiKey:$apiKey' in opencode
    oauth_selection = opencode[opencode.index('if [[ "$model" == openai/* ]] && [[ -n') :]
    assert oauth_selection.index('configure_openai_model "$model"') < oauth_selection.index('exit 0')
    assert oauth_selection.index('OPENCODE_CONFIG_CONTENT=%s') < oauth_selection.index('exit 0')


def test_coordinator_retries_only_confirmed_opencode_go_balance_failures():
    opencode = (Path(__file__).parents[1] / ".github/workflows/opencode.yml").read_text()
    retry = opencode[opencode.index("Confirmed OpenCode Go balance/usage limit"):]
    assert '.reason == "balance" and (.model | startswith("opencode-go-"))' in opencode
    assert "^opencode/[^[:space:]]*(big-pickle|(-free|:free))$" in opencode
    assert 'grep -Ev "${deny_re:-^$}"' in opencode
    assert 'echo "quota_retry_model=${MODEL}" >> "$GITHUB_OUTPUT"' in retry
    assert "retrying once with verified free model" in retry
def test_setup_opencode_pins_cli_version_verified_with_chatgpt_oauth():
    root = Path(__file__).parents[1]
    setup = (root / ".github/actions/setup-opencode/action.yml").read_text()
    assert "npm install --global @opencode/cli@2.2.5" in setup


def test_setup_opencode_refreshes_tokens_outside_opencode_and_starts_aisix():
    root = Path(__file__).parents[1]
    setup = (root / ".github/actions/setup-opencode/action.yml").read_text()
    assert 'python3 "$AGENT_RUNNER_SCRIPTS/refresh-token.py"' in setup
    assert "# renovate: datasource=docker depName=ghcr.io/api7/aisix\n        AISIX_VERSION=1.8.0" in setup
    assert 'AISIX_IMAGE="ghcr.io/api7/aisix:$AISIX_VERSION"' in setup
    assert "CHATGPT_ACCESS_TOKEN" in setup
    assert "CHATGPT_REFRESH_TOKEN" in setup
    assert "Materialize OpenCode auth" not in setup


def test_selection_tail_requires_fast_tool_free_selection():
    tail = (Path(__file__).parents[1] / "scripts/issue-selection-tail.txt").read_text()
    agent = (Path(__file__).parents[1] / ".opencode/agent/issue-selector.md").read_text()
    assert "End with the exact final line: SELECTED_ISSUE: <number>" in tail
    assert "No text may follow that line." in tail
    assert "Candidate order is random and carries no priority." in tail
    assert '  "*": deny' in agent
    assert "do not run commands, browse, fetch URLs, or look up issues or pull requests during selection" in agent
    assert "Choose ONE issue for one automated agent run." in tail
    assert "already filtered to one tier" in tail
