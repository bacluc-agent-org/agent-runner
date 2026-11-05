"""Tests for update_progress_comment: gh is faked, real process spawning is forbidden."""

import json
import re

import pytest

import update_progress_comment as upc

REPO = "bacluc-agent/agent-todo"
HEADINGS = (
    "### Management summary",
    "### Design decisions",
    "### Open PR",
    "### Verification evidence",
    "### Action runs",
)
OLD_URL = "https://github.com/bacluc-agent/agent-runner/actions/runs/100"
RUN_URL = "https://github.com/bacluc-agent/agent-runner/actions/runs/200"
PR_URL = "https://github.com/bacluc-agent/agent-runner/pull/7"
FORK_PR_URL = "https://github.com/bacluc-agent/paratest/pull/1"


def _comment(comment_id, body, login="bacluc-agent", created_at="2026-09-26T20:00:00Z"):
    return {
        "id": comment_id,
        "html_url": f"https://github.com/{REPO}/issues/287#issuecomment-{comment_id}",
        "body": body,
        "user": {"login": login},
        "created_at": created_at,
    }


def _is_data_row(line):
    return line.startswith("| ") and not line.startswith("| Run") and not set(line) <= set("| -")


def _rows(body):
    return [line for line in body.split("\n") if _is_data_row(line)]


def _cells(row):
    """The five cells of a row; the empty last cell renders as '|  |'."""
    return [cell.strip() for cell in row.split("|")[1:-1]]


def _without_rows(body):
    return "\n".join(line for line in body.split("\n") if not _is_data_row(line))


def _body_with_rows(*rows):
    lines = upc.TEMPLATE.read_text(encoding="utf-8").split("\n")
    header = next(i for i, line in enumerate(lines) if line.startswith("| Run"))
    lines[header + 2 : header + 2] = list(rows)
    return "\n".join(lines)


def _no_subprocess(*args, **kwargs):
    raise AssertionError("test must not shell out")


class FakeGh:
    """Answers only the endpoints update_progress_comment uses; records every call."""

    def __init__(self, comments=(), login="bacluc-agent"):
        self.comments = list(comments)
        self.login = login
        self.calls = []
        self.written = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("api", "user"):
            return self.login + "\n"
        if args[0] == "api" and args[1] == "--paginate" and args[2].startswith("repos/"):
            return json.dumps(self.comments) + "\n"
        if args[:3] == ("api", "-X", "POST") and args[3].endswith("/comments"):
            body = self._body(args)
            comment = _comment(max([c["id"] for c in self.comments] + [0]) + 1, body)
            self.comments.append(comment)
            self.written.append(body)
            return json.dumps(comment) + "\n"
        if args[:3] == ("api", "-X", "PATCH") and "/issues/comments/" in args[3]:
            comment = next(c for c in self.comments if str(c["id"]) in args[3])
            body = self._body(args)
            comment["body"] = body
            self.written.append(body)
            return json.dumps(comment) + "\n"
        raise AssertionError(f"unexpected gh call: {args}")

    def _body(self, args):
        field = next(arg for arg in args if arg.startswith("body=@"))
        with open(field.removeprefix("body=@"), encoding="utf-8") as handle:
            return handle.read()

    @property
    def mutating_calls(self):
        return [call for call in self.calls if call[0] == "api" and call[1] == "-X"]

    @property
    def list_calls(self):
        return [call for call in self.calls if call[1:2] == ("--paginate",)]


@pytest.fixture
def fake_gh(monkeypatch):
    def install(comments=(), login="bacluc-agent"):
        gh = FakeGh(comments, login)
        monkeypatch.setattr(upc, "run_gh", gh)
        monkeypatch.setattr(upc.subprocess, "run", _no_subprocess)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        return gh

    return install


def _argv(*extra):
    return [
        "--issue",
        "287",
        "--repo",
        REPO,
        "--model",
        "opencode/big-pickle",
        "--run-url",
        RUN_URL,
        *extra,
    ]


class TestTemplate:
    def test_structure(self):
        lines = upc.TEMPLATE.read_text(encoding="utf-8").split("\n")
        assert lines[0] == "<!-- agent-progress -->"
        for heading in HEADINGS:
            assert lines.count(heading) == 1, heading
        positions = [lines.index(heading) for heading in HEADINGS]
        assert positions == sorted(positions)
        header = lines.index("| Run | Model | What happened | Result | Failure reason |")
        # Padding-agnostic: prettier pads the separator to the header column widths.
        assert re.match(r"^\|\s*-+(\s*\|\s*-+){4}\s*\|$", lines[header + 1]), lines[header + 1]
        details = lines.index("<details><summary>Context for subsequent runs</summary>")
        assert details > header and lines.index("</details>") > details

    def test_placeholders_cannot_pass_as_the_marker(self):
        for line in upc.TEMPLATE.read_text(encoding="utf-8").split("\n"):
            if line.startswith("<!-- agent-progress:"):
                assert "<!-- agent-progress -->" not in line


class TestFinish:
    def test_replaces_pending_row_and_keeps_everything_else(self, fake_gh, tmp_path):
        existing = _comment(
            333,
            _body_with_rows(
                f"| {OLD_URL} | opencode/big-pickle | no branch | ✅ completed |  |",
                f"| {RUN_URL} | opencode/big-pickle | run started | pending |  |",
            ),
        )
        gh = fake_gh([existing])
        output = tmp_path / "github-output.txt"

        assert (
            upc.main(
                _argv(
                    "--run-summary",
                    "second dispatch",
                    "--result",
                    "✅ completed",
                    "--mode",
                    "finish",
                    "--github-output",
                    str(output),
                )
            )
            == 0
        )

        assert [call[:4] for call in gh.mutating_calls] == [
            ("api", "-X", "PATCH", f"repos/{REPO}/issues/comments/333")
        ]
        assert len(gh.list_calls) == 1
        body = gh.written[0]
        assert body.count(RUN_URL) == 1
        assert _without_rows(body) == _without_rows(existing["body"])
        rows = _rows(body)
        assert [row.split("|")[1].strip() for row in rows] == [OLD_URL, RUN_URL]
        assert all(row.count("|") == 6 for row in rows)
        assert _cells(rows[1]) == [
            RUN_URL,
            "opencode/big-pickle",
            "second dispatch",
            "✅ completed",
            "",
        ]
        assert "comment_id=333" in output.read_text()

    def test_sets_open_pr_section_for_every_pr(self, fake_gh):
        gh = fake_gh([_comment(333, upc.TEMPLATE.read_text(encoding="utf-8"))])

        assert (
            upc.main(
                _argv(
                    "--result",
                    "✅ completed",
                    "--pr-url",
                    f"{FORK_PR_URL} {PR_URL}",
                    "--mode",
                    "finish",
                )
            )
            == 0
        )

        body = gh.written[0]
        section = body.split("### Open PR\n")[1].split("\n### ")[0]
        assert section.strip().split("\n") == [FORK_PR_URL, PR_URL]
        assert "the workflow replaces this line" not in body

    def test_keeps_placeholder_when_no_pr_url(self, fake_gh):
        gh = fake_gh([_comment(333, upc.TEMPLATE.read_text(encoding="utf-8"))])

        assert upc.main(_argv("--result", "✅ completed", "--pr-url", "", "--mode", "finish")) == 0

        body = gh.written[0]
        assert (
            "the workflow replaces this line"
            in body.split("### Open PR\n")[1].split("\n### ")[0]
        )

    def test_creates_comment_when_missing(self, fake_gh):
        gh = fake_gh(
            [_comment(999, "unrelated", login="someone-else", created_at="2026-09-27T00:00:00Z")]
        )

        assert (
            upc.main(_argv("--result", "⚠️ failed (coordinator exit failure)", "--mode", "finish"))
            == 0
        )

        body = gh.written[0]
        assert body.split("\n")[0] == "<!-- agent-progress -->"
        assert [_cells(row) for row in _rows(body)] == [
            [
                RUN_URL,
                "opencode/big-pickle",
                "run started",
                "⚠️ failed (coordinator exit failure)",
                "",
            ]
        ]


class TestEnsure:
    def test_never_duplicates_an_existing_marker_comment(self, fake_gh, tmp_path):
        mine = _comment(333, upc.TEMPLATE.read_text(encoding="utf-8"))
        foreign = _comment(
            111,
            "<!-- agent-progress -->\n\nnot mine",
            login="someone-else",
            created_at="2026-09-26T23:00:00Z",
        )
        human = _comment(
            222, "please also update the readme", login="BacLuc", created_at="2026-09-26T22:00:00Z"
        )
        gh = fake_gh([mine, foreign, human])
        output = tmp_path / "github-output.txt"

        assert upc.main(_argv("--mode", "ensure", "--github-output", str(output))) == 0

        assert gh.written == []
        assert len(gh.list_calls) == 1
        assert output.read_text() == f"comment_id=333\ncomment_url={mine['html_url']}\n"

    def test_creates_from_template_when_no_marker_comment(self, fake_gh, tmp_path):
        gh = fake_gh([_comment(999, "unrelated", login="someone-else")])
        output = tmp_path / "github-output.txt"

        assert upc.main(_argv("--mode", "ensure", "--github-output", str(output))) == 0

        assert len(gh.mutating_calls) == 1
        assert gh.mutating_calls[0][:3] == ("api", "-X", "POST")
        assert [_cells(row) for row in _rows(gh.written[0])] == [
            [RUN_URL, "opencode/big-pickle", "run started", "pending", ""]
        ]
        assert "comment_id=1000" in output.read_text()


class TestErrors:
    def test_missing_repo_exits_1(self, fake_gh, monkeypatch):
        fake_gh()
        monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)

        assert upc.main(["--issue", "287", "--run-url", RUN_URL, "--mode", "ensure"]) == 1

    def test_gh_failure_exits_1(self, monkeypatch):
        def boom(*args):
            raise upc.subprocess.CalledProcessError(1, ["gh", *args])

        monkeypatch.setattr(upc, "run_gh", boom)

        assert upc.main(_argv("--mode", "ensure")) == 1
