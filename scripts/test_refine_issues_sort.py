from pathlib import Path

REFINE_WORKFLOW = Path(__file__).parents[1] / ".github/workflows/refine-issues.yml"


def test_refine_workflow_search_sorts_oldest_first_via_query_params():
    content = REFINE_WORKFLOW.read_text()
    # sort must be a real query parameter, not an (ignored) qualifier inside q
    assert "sort:created-asc" not in content
    assert (
        'gh api "search/issues?q=repo:${ISSUE_REPOSITORY}+is:open+is:issue+-label:agent-refined+-label:ready-for-implementation+-label:agent-ignore&sort=created&order=asc&per_page=5"'
        in content
    )
    assert "&sort=created&order=asc&per_page=5" in content
