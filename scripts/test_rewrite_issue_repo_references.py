"""Tests for rewrite_issue_repo_references: gh is faked, real process spawning is forbidden."""

import json

import pytest

import rewrite_issue_repo_references as rir

REPO = "bacluc-agent/agent-todo"
OLD = "bacluc-agent/agent-runner"
NEW = "bacluc-agent-org/agent-runner"


def _no_subprocess(*args, **kwargs):
    raise AssertionError("test must not shell out")


def _issue(number, title, body):
    return {"number": number, "title": title, "body": body}


class FakeGh:
    """Answers only the endpoints rewrite_issue_repo_references uses; records every call."""

    def __init__(self, issues):
        self.issues = [dict(issue) for issue in issues]
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("issue", "list"):
            return json.dumps(self.issues) + "\n"
        if args[:2] == ("issue", "view"):
            number = int(args[2])
            return json.dumps(next(i for i in self.issues if i["number"] == number)) + "\n"
        if args[:2] == ("issue", "edit"):
            issue = next(i for i in self.issues if i["number"] == int(args[2]))
            issue["title"] = args[args.index("--title") + 1]
            with open(args[args.index("--body-file") + 1], encoding="utf-8") as handle:
                issue["body"] = handle.read()
            return json.dumps(issue) + "\n"
        raise AssertionError(f"unexpected gh call: {args}")

    @property
    def mutating_calls(self):
        return [call for call in self.calls if call[:2] == ("issue", "edit")]


@pytest.fixture
def fake_gh(monkeypatch):
    def install(issues):
        gh = FakeGh(issues)
        monkeypatch.setattr(rir, "run_gh", gh)
        monkeypatch.setattr(rir.subprocess, "run", _no_subprocess)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        return gh

    return install


@pytest.mark.parametrize(
    "text",
    [
        f"https://github.com/{OLD}/actions/runs/100",
        f"https://github.com/{OLD}/pull/7",
        f"https://github.com/{OLD}/issues/9",
        f"see {OLD}-2 build",
        f"see {OLD}#127",
        f"gh run list --repo {OLD}",
        f"gh run view 1 -R {OLD}",
        f"repos/{OLD}/pulls/1",
        f"repository: {OLD}",
        f"the old repo {OLD} stays historical",
        f"link returned 404 in the org fork: {OLD}",
        f"opened PR in {OLD}",
        f"reviewed runs from {OLD}",
    ],
)
def test_preserved(text):
    assert rir.rewrite_text(text) == (text, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "the repo is bacluc-agent/agent-runner here",
            "the repo is bacluc-agent-org/agent-runner here",
        ),
        (
            "https://github.com/bacluc-agent/agent-runner",
            "https://github.com/bacluc-agent-org/agent-runner",
        ),
        (
            "target repo `bacluc-agent/agent-runner`, issue repo",
            "target repo `bacluc-agent-org/agent-runner`, issue repo",
        ),
        (
            "lands green in `bacluc-agent/agent-runner`",
            "lands green in `bacluc-agent-org/agent-runner`",
        ),
        (
            "Push the branch to `bacluc-agent/agent-runner` and open",
            "Push the branch to `bacluc-agent-org/agent-runner` and open",
        ),
        (
            "Fix bacluc-agent/agent-runner now",
            "Fix bacluc-agent-org/agent-runner now",
        ),
    ],
)
def test_rewritten(text, expected):
    assert rir.rewrite_text(text) == (expected, 1)


def test_idempotent():
    text = f"target repo `{OLD}` and https://github.com/{OLD}/actions/runs/1"
    once, count = rir.rewrite_text(text)
    assert (once, count) == (f"target repo `{NEW}` and https://github.com/{OLD}/actions/runs/1", 1)
    assert rir.rewrite_text(once) == (once, 0)


def test_noop_body_unchanged():
    text = "nothing to rewrite here"
    assert rir.rewrite_text(text) == (text, 0)


def test_rewrites_multiple_references():
    text = f"{OLD} and {NEW} and {OLD}"
    new, count = rir.rewrite_text(text)
    assert new == f"{NEW} and {NEW} and {NEW}"
    assert count == 2


def test_dry_run_makes_no_mutating_calls(fake_gh, capsys):
    gh = fake_gh([_issue(1, "plain title", f"body mentions {OLD} and {NEW}")])

    assert rir.main(["--repo", REPO]) == 0

    assert gh.mutating_calls == []
    assert "SCANNED=1 CHANGED=1" in capsys.readouterr().out


def test_apply_edits_only_changed_issues_and_is_idempotent(fake_gh, capsys):
    gh = fake_gh(
        [
            _issue(1, f"title {OLD}", f"body mentions {OLD} and https://github.com/{OLD}/pull/3"),
            _issue(2, "plain title", "no reference at all"),
            _issue(3, "plain title", f"only historical https://github.com/{OLD}/actions/runs/1"),
        ]
    )

    assert rir.main(["--repo", REPO, "--apply"]) == 0

    assert [call[2] for call in gh.mutating_calls] == ["1"]
    assert gh.issues[0]["title"] == f"title {NEW}"
    assert gh.issues[0]["body"] == f"body mentions {NEW} and https://github.com/{OLD}/pull/3"
    assert "SCANNED=3 CHANGED=1" in capsys.readouterr().out

    gh.calls.clear()
    assert rir.main(["--repo", REPO, "--apply"]) == 0

    assert gh.mutating_calls == []
    assert "SCANNED=3 CHANGED=0" in capsys.readouterr().out


def test_issue_option_views_one_issue(fake_gh, capsys):
    gh = fake_gh([_issue(7, "t", f"body {OLD}"), _issue(8, "t", f"body {OLD}")])

    assert rir.main(["--repo", REPO, "--issue", "8"]) == 0

    assert gh.mutating_calls == []
    assert "SCANNED=1 CHANGED=1" in capsys.readouterr().out
