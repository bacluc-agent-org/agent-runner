from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

from selection_tiers import Candidate, parse_branches, render_prompt

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/hourly-issue.yml"


def candidate(number=1, **values):
    defaults = dict(
        number=number,
        title="Issue",
        labels=[],
        created_at="2026-01-01T00:00:00Z",
        attempts=0,
        recent_attempts=0,
    )
    defaults.update(values)
    return Candidate(**defaults)


def test_branch_history_and_recent_repeat():
    branches = ["agent-run/330-37135909176", "issue-2-37135909177", "invalid"]
    assert parse_branches(branches) == [(2, 37135909177), (330, 37135909176)]
    assert not any(__import__("re").search(r"issue-[0-9]+", branch) for branch in branches[:1])
    assert candidate(330, attempts=1).attempts == 1
    assert candidate(9, attempts=3, recent_attempts=1).recent_attempts == 1


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
    assert "[attempts: 1] [recent-attempts: 0]" in result.stdout


def test_render_prompt_randomizes_all_candidates(monkeypatch):
    better, awaiting = candidate(2), candidate(3)
    big = candidate(4)
    big.body = "x" * 3000
    big.labels = ["many", "labels"]
    import random

    monkeypatch.setattr(random, "shuffle", lambda items: items.reverse())
    prompt = render_prompt([better, awaiting], "Reply\nSELECTED_ISSUE: <number>")
    assert prompt.index("3: Issue") < prompt.index("2: Issue")
    assert "[attempts: 0] [recent-attempts: 0]" in prompt
    assert "avoid" not in prompt.lower()
    assert prompt.rstrip().endswith("SELECTED_ISSUE: <number>")


def test_workflow_fetches_all_search_pages_and_passes_all_tiers():
    workflow = WORKFLOW.read_text()
    assert 'gh api --paginate --slurp "search/issues?' in workflow
    assert '> "$search_json.pages"' in workflow
    assert 'jq \'[.[] | .items[]]\' "$search_json.pages" > "$search_json"' in workflow
    assert "--jq" not in next(
        line for line in workflow.splitlines() if "search/issues?" in line
    )
    assert "&per_page=100\"" in workflow
    assert "sort:created-asc" not in workflow
    tail = (Path(__file__).parents[1] / "scripts/issue-selection-tail.txt").read_text()
    assert "includes all eligible issues" in tail
    assert "do not favor new issues solely because they are unattempted" in tail


def test_hourly_workflow_logs_lookup_and_selector_durations():
    content = WORKFLOW.read_text()
    for label in (
        "GitHub lookup duration: candidate search and collection",
        "GitHub lookup duration: repository branches",
        "Issue selector duration:",
    ):
        assert label in content


def test_hourly_selection_uses_candidate_metadata_without_preselection_research():
    content = WORKFLOW.read_text()
    assert "gh pr list -R" not in content
    assert 'gh issue view "$num"' not in content
    assert 'gh pr view "$pr_number"' not in content
    assert 'gh api --paginate --slurp "repos/$pr_repo/pulls/$pr_number/commits"' not in content
    assert 'gh issue edit "$issue_number" -R "$ISSUE_REPOSITORY" --add-label agent-running,agent-attempted' in content
    selector = (Path(__file__).parent / "selection_tiers.py").read_text()
    assert "issue_runs = [run for candidate, run in runs if candidate == number]" in selector
    assert "[PR:" not in selector
    assert "[last-human-feedback:" not in selector


def test_selector_retries_invocation_failures_on_distinct_eligible_models_only():
    content = WORKFLOW.read_text()
    retry = content[content.index('selection="$RUNNER_TEMP/selection.out"'):content.index("if ! grep -q '[^[:space:]]' \"$selection\"")]
    assert "while (( selection_status != 0 && selection_retry_count < 2 ))" in retry
    assert "((selection_retry_count+=1))" in retry
    assert "((selection_retry_count++))" not in retry
    assert "retry_candidates=(" in retry
    assert '[[ "$candidate" == "$selection_model" ]] && continue' in retry
    assert 'grep -Fxq "$candidate" "$available_models_file"' in retry
    assert '[[ -n "$OPENROUTER_API_KEY" ]] || continue' in retry
    assert "grep -Ev -f scripts/model-deny-list.txt" in retry
    assert '[[ -n "$OPENCODE_GO_API_KEY" ]] || continue' in retry
    assert '[[ -n "$OPENCODE_GO_2_API_KEY" ]] || continue' in retry
    assert "selection_status != 0" in retry
    assert 'if (( selection_status == 0 )) && [[ -f "$RUNNER_TEMP/opencode-fatal.json" ]]; then' in retry
    assert "! grep -Eq '^SELECTED_ISSUE:" in retry
    assert 'sleep 0.1' in retry
    assert "selection_status=1" in retry
    assert content.index("if ! grep -q '[^[:space:]]' \"$selection\"") > content.index("if (( selection_status != 0 )); then")


def test_issue_selection_and_refinement_can_choose_verified_openai_oauth_models():
    root = Path(__file__).parents[1]
    for workflow_name, empty_message in (
        ("hourly-issue.yml", "No issue-selection model is available"),
        ("refine-issues.yml", "No refinement model is available"),
    ):
        content = (root / ".github/workflows" / workflow_name).read_text()
        assert "OPENCODE_AUTH_CONTENT: ${{ secrets.OPENCODE_AUTH_JSON }}" in content
        assert "mapfile -t openai_models < <(grep -E '^openai/'" in content
        assert '"${openai_models[@]}"' in content
        assert "openai/*)" in content
        assert '$HOME/.local/share/opencode/auth.json' in content
        assert empty_message in content
        assert 'configure_openai_model "$selection_model"' in content
        assert 'if [[ "$selection_model" == openai/* ]]; then' in content
        assert 'opencode run "${standalone_args[@]}" --agent' in content

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
    assert 'opencode run "${coordinator_standalone_args[@]}" --agent coordinator' in opencode
    oauth_selection = opencode[opencode.index('if [[ "$model" == openai/* ]] && jq'):]
    assert oauth_selection.index('configure_openai_model "$model"') < oauth_selection.index('exit 0')
    assert oauth_selection.index('OPENCODE_CONFIG_CONTENT=%s') < oauth_selection.index('exit 0')


def test_setup_opencode_pins_cli_version_verified_with_chatgpt_oauth():
    root = Path(__file__).parents[1]
    setup = (root / ".github/actions/setup-opencode/action.yml").read_text()
    assert "npm install --global @opencode/cli@2.2.5" in setup


def test_selection_tail_requires_fast_tool_free_selection():
    tail = (Path(__file__).parents[1] / "scripts/issue-selection-tail.txt").read_text()
    agent = (Path(__file__).parents[1] / ".opencode/agent/issue-selector.md").read_text()
    assert "End with the exact final line: SELECTED_ISSUE: <number>" in tail
    assert "No text may follow that line." in tail
    assert "Do not use tools or perform research." in tail
    assert '  "*": deny' in agent
    assert "do not run commands, browse, fetch URLs, or look up issues or pull requests during selection" in agent
    assert "choose the best-so-far candidate" in tail
    assert 'note the doubt inside the implementation prompt' in tail
