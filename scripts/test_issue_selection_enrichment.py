import json
import os
from pathlib import Path
from subprocess import run

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/hourly-issue.yml"

COMMENTS = [
    {
        "number": 101,
        "comments": [
            {"author": {"login": "bacluc-agent"}, "createdAt": "2026-01-03T00:00:00Z"},
            {"author": {"login": "alice"}, "createdAt": "2026-01-02T00:00:00Z"},
        ],
    },
    {"number": 102, "comments": []},
]

GH_STUB = """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" >> "$GH_CALL_LOG"
case "$1 $2" in
  "api repos/"*) printf 'main\\n' ;;
  "repo view") printf '%s\\n' "$3" ;;
  "pr list") : ;;
  "issue list") cat "$GH_COMMENTS_JSON" ;;
  *)
    printf 'unexpected gh call: %s\\n' "$*" >&2
    exit 1
    ;;
esac
"""


def _enrichment_block():
    text = WORKFLOW.read_text()
    return text[text.index("# enrichment-start") : text.index("# enrichment-end")]


def _run_enrichment(tmp_path):
    search_json = tmp_path / "candidates.json"
    search_json.write_text(
        json.dumps(
            [
                {
                    "number": 101,
                    "body": (
                        "Fixes https://github.com/ecamp/ecamp3/issues/1. "
                        "Paths: api/camps, Entity/Camp.php, e2e/tests"
                    ),
                },
                {
                    "number": 102,
                    "body": "Upstream git@github.com:bacluc/provision-machines.git.",
                },
            ]
        )
    )
    candidates = tmp_path / "candidate-numbers.txt"
    candidates.write_text("101\n102\n")
    comments_json = tmp_path / "comments.json"
    comments_json.write_text(json.dumps(COMMENTS))

    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)

    calls_log = tmp_path / "gh-calls.log"
    script = (
        f"search_json={str(search_json)!r}\n"
        f"candidates={str(candidates)!r}\n"
        f"RUNNER_TEMP={str(tmp_path)!r}\n"
        "GITHUB_REPOSITORY=bacluc-agent-org/agent-runner\n"
        "ISSUE_REPOSITORY=bacluc-agent/agent-todo\n"
        'gh_retry() { "$@"; }\n'
        f"{_enrichment_block()}\n"
    )
    result = run(
        ["bash", "-Eeuo", "pipefail", "-c", script],
        env={
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "GH_CALL_LOG": str(calls_log),
            "GH_COMMENTS_JSON": str(comments_json),
        },
        capture_output=True,
        text=True,
    )
    return result, calls_log


def test_enrichment_batches_comments_and_skips_path_like_body_fragments(tmp_path):
    result, calls_log = _run_enrichment(tmp_path)
    assert result.returncode == 0, result.stderr

    calls = calls_log.read_text().splitlines()
    assert not any(call.startswith("issue view") for call in calls)
    assert sum(call.startswith("issue list") for call in calls) == 1

    repo_views = [call for call in calls if call.startswith("repo view")]
    assert any("ecamp/ecamp3" in call for call in repo_views)
    assert any("bacluc/provision-machines" in call for call in repo_views)
    for fragment in ("api/camps", "Entity/Camp.php", "e2e/tests"):
        assert not any(fragment in call for call in calls), fragment

    enriched = json.loads((tmp_path / "candidates.final.json").read_text())
    by_number = {issue["number"]: issue for issue in enriched}
    assert by_number[101]["last_human_comment_at"] == "2026-01-02T00:00:00Z"
    assert by_number[102]["last_human_comment_at"] is None
