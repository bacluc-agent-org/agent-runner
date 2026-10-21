import json
import os
import re
import subprocess
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/review-fixes.yml"
STUB_GH = """#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "$1" == api ]]; then
  printf '%s\\n' "$STUB_PRS"
elif [[ "$1" == pr && "$2" == view ]]; then
  jq -r --arg key "$5#$3" '.[$key]' "$STUB_METADATA"
else
  echo "unexpected gh call: $*" >&2
  exit 1
fi
"""


def find_prs_script() -> str:
    lines = WORKFLOW.read_text().splitlines()
    start = lines.index("      - name: Find PRs with review comments")
    run_start = next(
        i for i in range(start, len(lines)) if lines[i] == "        run: |"
    )
    block = []
    for line in lines[run_start + 1 :]:
        if line.strip() and not line.startswith("          "):
            break
        block.append(line[10:])
    return "\n".join(block) + "\n"


def run_find_prs(tmp_path, event, ref, metadata):
    gh_dir = tmp_path / "bin"
    gh_dir.mkdir()
    gh = gh_dir / "gh"
    gh.write_text(STUB_GH)
    gh.chmod(0o755)
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text(json.dumps(metadata))
    output = tmp_path / "output"
    output.touch()
    env = {
        **os.environ,
        "PATH": f"{gh_dir}:{os.environ['PATH']}",
        "GITHUB_TOKEN": "token",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF_NAME": ref,
        "GITHUB_REF": f"refs/heads/{ref}",
        "GITHUB_REPOSITORY": "example/repo",
        "SEARCH_OWNER": "bacluc-agent",
        "OVERRIDE_PROMPT": "",
        "RUNNER_TEMP": str(tmp_path),
        "STUB_PRS": "\n".join(metadata) + "\n",
        "STUB_METADATA": str(metadata_file),
    }
    script = tmp_path / "find_prs.sh"
    script.write_text(find_prs_script())
    result = subprocess.run(
        ["bash", "-Eeuo", "pipefail", str(script)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    text = output.read_text()
    prs = json.loads(re.search(r"prs<<PRS_EOF\n(.*?)\nPRS_EOF", text, re.S).group(1))
    count = int(re.search(r"^count=(\d+)$", text, re.M).group(1))
    return result, prs, count, text


def pr(number, head, repo="example/repo"):
    return {
        "number": number,
        "title": f"PR {number}",
        "headRefName": head,
        "url": f"https://github.com/{repo}/pull/{number}",
    }


OTHER = pr(1, "unrelated-branch")
TARGET = pr(2, "feature-branch")


def test_push_dispatches_only_matching_branch(tmp_path):
    metadata = {"example/repo#1": OTHER, "example/repo#2": TARGET}
    result, prs, count, text = run_find_prs(tmp_path, "push", "feature-branch", metadata)
    assert prs == ["example/repo#2"]
    assert count == 1
    assert "example/repo#2" in text
    assert "Skipping PR example/repo#1: head branch unrelated-branch" in result.stderr


def test_push_skips_same_branch_in_other_repository(tmp_path):
    metadata = {
        "example/repo#2": TARGET,
        "other/repo#3": pr(3, "feature-branch", repo="other/repo"),
    }
    result, prs, count, text = run_find_prs(tmp_path, "push", "feature-branch", metadata)
    assert prs == ["example/repo#2"]
    assert count == 1
    assert "Skipping PR other/repo#3: repository other/repo" in result.stderr
    metadata_out = json.loads(
        re.search(r"pr_metadata<<META_EOF\n(.*?)\nMETA_EOF", text, re.S).group(1)
    )
    assert sorted(metadata_out) == prs


def test_push_without_matching_branch_dispatches_nothing(tmp_path):
    metadata = {"example/repo#1": OTHER}
    result, prs, count, _ = run_find_prs(tmp_path, "push", "feature-branch", metadata)
    assert prs == []
    assert count == 0
    assert "No viewable open PRs with review comments found." in result.stdout


def test_workflow_dispatch_keeps_all_prs(tmp_path):
    metadata = {"example/repo#1": OTHER, "example/repo#2": TARGET}
    result, prs, count, _ = run_find_prs(tmp_path, "workflow_dispatch", "main", metadata)
    assert prs == ["example/repo#1", "example/repo#2"]
    assert count == 2
    assert "Skipping PR" not in result.stderr
