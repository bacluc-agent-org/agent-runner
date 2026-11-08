import json
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

from issue_comment_eligibility import is_refined_feedback_candidate, qualifying_comments


def comment(login, created_at, body="", user_type="User"):
    return {
        "user": {"login": login, "type": user_type},
        "created_at": created_at,
        "body": body,
    }


def test_qualifying_comments_exclude_agent_progress_and_bots_and_require_newer_timestamp():
    comments = [
        comment("bacluc-agent", "2026-11-08T10:01:00Z", "agent reply"),
        comment("human", "2026-11-08T10:02:00Z", "<!-- agent-progress -->\nrun"),
        comment("github-actions", "2026-11-08T10:03:00Z", "feedback", "Bot"),
        comment("human", "2026-11-08T10:04:00Z", "new feedback"),
    ]
    assert qualifying_comments(comments, "2026-11-08T10:03:00Z") == [comments[3]]


def test_label_eligibility_requires_refined_ready_and_not_ignored():
    feedback = [comment("human", "2026-11-08T10:05:00Z", "feedback")]
    assert is_refined_feedback_candidate(
        ["agent-refined", "ready-for-implementation"], feedback, "2026-11-08T10:04:00Z"
    )
    assert not is_refined_feedback_candidate(
        ["agent-refined"], feedback, "2026-11-08T10:04:00Z"
    )
    assert not is_refined_feedback_candidate(
        ["agent-refined", "ready-for-implementation", "agent-ignore"],
        feedback,
        "2026-11-08T10:04:00Z",
    )


def test_configured_agent_identity_is_excluded():
    own = comment("custom-agent", "2026-11-08T10:04:00Z", "self reply")
    human = comment("human", "2026-11-08T10:05:00Z", "feedback")
    assert qualifying_comments([own, human], agent_login="custom-agent") == [human]


def test_cli_is_executable_and_returns_no_eligible_issue_as_failure():
    with TemporaryDirectory() as directory:
        comments = Path(directory) / "comments.json"
        output = Path(directory) / "eligible.json"
        comments.write_text(json.dumps([comment("bacluc-agent", "2026-11-08T10:04:00Z")]))
        result = run(
            [
                "python3",
                "scripts/issue_comment_eligibility.py",
                "--comments",
                str(comments),
                "--after",
                "2026-11-08T10:03:00Z",
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert json.loads(output.read_text()) == []
