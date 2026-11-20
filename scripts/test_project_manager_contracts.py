"""Contract tests for the project-manager workflows and agent definitions."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KANBAN_WF = ROOT / ".github/workflows/kanban-manager.yml"
STANDING_WF = ROOT / ".github/workflows/standing-issue-manager.yml"
KANBAN_AGENT = ROOT / ".opencode/agent/kanban-manager.md"
STANDING_AGENT = ROOT / ".opencode/agent/standing-issue-manager.md"
OPENCODE_WF = ROOT / ".github/workflows/opencode.yml"


def read(path):
    return path.read_text()


def test_workflows_have_cron_and_dispatch():
    for path in (KANBAN_WF, STANDING_WF):
        text = read(path)
        assert "cron:" in text
        assert '"0 * * * *"' in text or "0 * * * *" in text
        assert "workflow_dispatch:" in text


def test_workflows_are_thin_callers():
    for path in (KANBAN_WF, STANDING_WF):
        text = read(path)
        assert "uses: ./.github/workflows/opencode.yml" in text
        assert "secrets: inherit" in text


def test_concurrency_groups():
    assert "group: kanban-project-manager" in read(KANBAN_WF)
    assert "group: standing-issue-project-manager" in read(STANDING_WF)
    for path in (KANBAN_WF, STANDING_WF):
        text = read(path)
        assert "cancel-in-progress: false" in text


def test_guards_and_agent_names():
    kanban = read(KANBAN_WF)
    standing = read(STANDING_WF)
    assert "vars.KANBAN_PROJECT_NUMBER != ''" in kanban
    assert "vars.STANDING_PROJECT_NUMBER != ''" in standing
    assert "agent: kanban-manager" in kanban
    assert "agent: standing-issue-manager" in standing


def test_project_variables_and_default_five():
    for path in (KANBAN_WF, STANDING_WF):
        text = read(path)
        assert "bacluc-agent-org" in text
        assert "vars.ISSUE_REPOSITORY || github.repository" in text
        assert "vars.MAX_AGENT_DISPATCHES || '5'" in text


def test_repository_projects_write_only_in_manager_workflows():
    workflows = list((ROOT / ".github/workflows").glob("*.yml"))
    for path in workflows:
        text = read(path)
        if "repository-projects: write" in text:
            assert path.name in {"kanban-manager.yml", "standing-issue-manager.yml", "opencode.yml"}, path.name


def test_agent_wiring_and_frontmatter():
    for path in (KANBAN_AGENT, STANDING_AGENT):
        text = read(path)
        assert "mode: primary" in text
        assert "temperature: 0.1" in text
        assert '"*": deny' in text
        assert "read: allow" in text
        assert "glob: allow" in text
        assert "grep: allow" in text
        assert "webfetch: allow" in text
        assert "bash:" in text


def test_kanban_six_statuses():
    text = read(KANBAN_AGENT)
    for status in ["backlog", "prioritized", "refined", "in progress", "agent review", "human review"]:
        assert status in text


def test_standing_move_and_swap_rules():
    text = read(STANDING_AGENT)
    assert "backlog" in text
    assert "end" in text
    assert "swap" in text.lower() or "second" in text.lower()
    assert "deferred" in text.lower() or "not due" in text.lower()
    assert "completed" in text.lower()


def test_deduplication():
    for path in (KANBAN_AGENT, STANDING_AGENT):
        text = read(path)
        assert "agent-running" in text
    kanban = read(KANBAN_AGENT)
    assert "deduplicat" in kanban.lower()


def test_no_direct_graphql():
    kanban = read(KANBAN_AGENT)
    standing = read(STANDING_AGENT)
    assert "Direct GraphQL is denied" in kanban
    assert "repository-approved guarded GraphQL path" in kanban
    assert "never call GraphQL directly" in standing
    assert "does not expose\nproject-v2 item order/priority" in kanban
    assert "may only read order/priority" in kanban
    assert "after a complete desired" in kanban
    assert "must not replace `gh project` or REST" in kanban


def test_reusable_manager_permission_boundary():
    text = read(OPENCODE_WF)
    assert "permissions:" in text
    assert "repository-projects: write" in text


def test_no_auto_close():
    for path in (KANBAN_AGENT, STANDING_AGENT):
        text = read(path)
        assert "Do not close" in text or "never auto-close" in text or "no auto-close" in text
        assert "gh issue close" not in text
    assert "Do not close issues" in read(KANBAN_AGENT)
    assert "Do not close issues automatically" in read(STANDING_AGENT)


def test_agent_dispatch_command():
    for path in (KANBAN_AGENT, STANDING_AGENT):
        text = read(path)
        assert "gh workflow run opencode.yml -R bacluc-agent-org/agent-runner" in text
        assert "agent=coordinator" in text
