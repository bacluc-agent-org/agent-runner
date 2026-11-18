import json
import os
import re
import shlex
import subprocess
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/refine-issues.yml"
COMMENT_START = 'comments_file="$RUNNER_TEMP/comments-${number}.txt"'
PROMPT_END = '} > "$prompt"'
SUCCESS_START = "if (( validation_status == 0 )); then"
SEARCH_START = 'search_json="$RUNNER_TEMP/candidates.json"'
CANDIDATES_END = 'cat "$candidates"'
EXIT_GUARD = "(( refined > 0 )) || exit 1"

FAKE_GH = r"""#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' "$*" >> "$GH_LOG"

if [[ "${1:-}" == "api" ]]; then
  shift
  jq_filter=""
  url=""
  while (($#)); do
    case "$1" in
      --jq) jq_filter="$2"; shift 2 ;;
      --paginate) shift ;;
      -*) shift ;;
      *) url="$1"; shift ;;
    esac
  done
  if [[ -n "${GH_FAIL_SEARCH:-}" && "$url" == *"search/issues"* ]]; then
    printf 'search failed\n' >&2
    exit 1
  fi
  if [[ "$url" == *"search/issues"* ]]; then
    fixture="$GH_FIXTURES/unrefined-search.json"
  elif [[ "$url" =~ /issues/([0-9]+)/comments ]]; then
    if [[ ",${GH_FAIL_COMMENTS:-}," == *",${BASH_REMATCH[1]},"* ]]; then
      printf 'comments failed\n' >&2
      exit 1
    fi
    fixture="$GH_FIXTURES/comments-${BASH_REMATCH[1]}.json"
  else
    printf 'unexpected api url: %s\n' "$url" >&2
    exit 1
  fi
  if [[ -n "$jq_filter" ]]; then
    jq -r "$jq_filter" "$fixture"
  else
    cat "$fixture"
  fi
  exit 0
fi

if [[ "${1:-}" == "issue" ]]; then
  shift
  command="${1:-}"
  shift || true
  if [[ "$command" == "view" ]]; then
    number="$1"
    shift || true
    jq_filter=""
    while (($#)); do
      case "$1" in
        --jq) jq_filter="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    fixture="$GH_FIXTURES/issue-${number}.json"
    if [[ -n "$jq_filter" ]]; then
      jq -r "$jq_filter" "$fixture"
    else
      cat "$fixture"
    fi
    exit 0
  fi
  if [[ "$command" == "edit" ]]; then
    exit 0
  fi
fi

if [[ "${1:-}" == "label" ]]; then
  exit 0
fi

printf 'unexpected gh invocation: %s\n' "$*" >&2
exit 1
"""

FAKE_OPENCODE = r"""#!/usr/bin/env bash
printf '## Goal\nDo the thing.\n\n## How to implement\nChange the code.\n'
"""


def extract_block(start_text, end_text):
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == start_text)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == end_text)
    return "\n".join(lines[start : end + 1])


def extract_if_block(start_text):
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(start_text))
    depth = 0
    seen = False
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if re.match(r"^if\b", stripped):
            depth += 1
            seen = True
        elif re.match(r"^fi\b", stripped):
            depth -= 1
        if seen and depth == 0:
            return "\n".join(lines[start : index + 1])
    raise AssertionError(f"unterminated block starting with {start_text!r}")


def comment_block():
    return extract_block(COMMENT_START, PROMPT_END)


def success_block():
    return extract_if_block(SUCCESS_START)


def candidate_block():
    return extract_block(SEARCH_START, CANDIDATES_END)


class WorkflowRun:
    def __init__(self, tmp_path, opencode_script=FAKE_OPENCODE):
        self.tmp_path = tmp_path
        self.fixtures = tmp_path / "fixtures"
        self.fixtures.mkdir()
        self.runner = tmp_path / "runner"
        self.runner.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        opencode = bin_dir / "opencode"
        opencode.write_text(opencode_script)
        opencode.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GH_FIXTURES": str(self.fixtures),
            "GH_LOG": str(tmp_path / "gh.log"),
            "RUNNER_TEMP": str(self.runner),
            "ISSUE_REPOSITORY": "bacluc-agent/agent-todo",
            "GITHUB_TOKEN": "test-token",
        }

    def write_json(self, name, value):
        (self.fixtures / name).write_text(json.dumps(value))

    def searches(self, unrefined=()):
        self.write_json("unrefined-search.json", {"items": [{"number": n} for n in unrefined]})

    def comments(self, number, comments):
        self.write_json(f"comments-{number}.json", comments)

    def run(self, script, fail_search=False, fail_comments=()):
        env = dict(self.env)
        if fail_search:
            env["GH_FAIL_SEARCH"] = "1"
        if fail_comments:
            env["GH_FAIL_COMMENTS"] = ",".join(str(number) for number in fail_comments)
        return subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=WORKFLOW.parents[2],
        )

    def candidates(self):
        path = self.runner / "candidate-numbers.txt"
        return [line for line in path.read_text().splitlines() if line]

    def gh_log(self):
        path = self.tmp_path / "gh.log"
        return path.read_text() if path.exists() else ""


def comment(login, created_at, body, user_type="User"):
    return {
        "user": {"login": login, "type": user_type},
        "created_at": created_at,
        "body": body,
    }


def prompt_script():
    return f"""
number=7
title="Vague issue"
body="Make it better"
{comment_block()}
printf 'PROMPT-BEGIN\\n'
cat "$prompt"
printf '\\nPROMPT-END\\n'
"""


def prompt_from(result):
    return result.stdout.split("PROMPT-BEGIN\n", 1)[1].split("\nPROMPT-END\n", 1)[0]


def test_human_comments_appear_in_prompt(tmp_path):
    run = WorkflowRun(tmp_path)
    run.comments(7, [comment("BacLuc", "2026-11-16T20:00:00Z", "Remove point 5, i am not ok with point 5.")])

    result = run.run(prompt_script())
    assert result.returncode == 0, result.stderr
    prompt = prompt_from(result)
    assert "Reviewer comments (feedback to fold into the Goal and steps; do not quote them wholesale):" in prompt
    assert "Author: BacLuc" in prompt
    assert "Remove point 5, i am not ok with point 5." in prompt


def test_bot_agent_and_progress_comments_never_appear(tmp_path):
    run = WorkflowRun(tmp_path)
    run.comments(
        7,
        [
            comment("some-bot", "2026-11-16T11:00:00Z", "bot feedback", user_type="Bot"),
            comment("bacluc-agent", "2026-11-16T12:00:00Z", "branch pushed"),
            comment("BacLuc", "2026-11-16T13:00:00Z", "<!-- agent-progress -->\nstatus"),
            comment("BacLuc", "2026-11-16T14:00:00Z", "genuine reviewer feedback"),
        ],
    )

    result = run.run(prompt_script())
    assert result.returncode == 0, result.stderr
    prompt = prompt_from(result)
    assert "genuine reviewer feedback" in prompt
    assert "bot feedback" not in prompt
    assert "branch pushed" not in prompt
    assert "status" not in prompt
    assert "Author: some-bot" not in prompt
    assert "Author: bacluc-agent" not in prompt


def test_agent_refined_label_readded_only_after_validation_success(tmp_path):
    run = WorkflowRun(tmp_path)
    block = success_block()
    out = tmp_path / "out.txt"
    out.write_text("refined body\n")

    def run_success(validation_status):
        script = f"""
number=7
out={shlex.quote(str(out))}
refined=0
failed=0
validation_status={validation_status}
while :; do
{block}
break
done
printf 'refined=%s failed=%s\\n' "$refined" "$failed"
"""
        return run.run(script)

    result = run_success(1)
    assert result.returncode == 0
    assert "refined=0 failed=0" in result.stdout
    assert "--add-label agent-refined" not in run.gh_log()

    result = run_success(0)
    assert result.returncode == 0
    assert "refined=1 failed=0" in result.stdout
    assert "--add-label agent-refined" in run.gh_log()


def test_no_marker_and_no_automatic_label_removal():
    content = WORKFLOW.read_text()
    assert "<!-- agent-refined:" not in content
    assert "--remove-label" not in content


def test_issue_with_removed_agent_refined_label_is_a_candidate(tmp_path):
    run = WorkflowRun(tmp_path)
    run.searches(unrefined=[454])

    result = run.run(candidate_block())
    assert result.returncode == 0, result.stderr
    assert run.candidates() == ["454"]
    gh_log = run.gh_log()
    assert "-label:agent-refined" in gh_log
    assert "-label:ready-for-implementation" in gh_log


def test_guard_lines_present_and_ordered():
    lines = WORKFLOW.read_text().splitlines()
    no_candidates = next(i for i, line in enumerate(lines) if "No unrefined open issues; nothing to do." in line)
    exit_guard = next(i for i, line in enumerate(lines) if line.strip() == EXIT_GUARD)
    assert no_candidates < exit_guard
    completion = next(i for i, line in enumerate(lines) if "Refinement complete:" in line)
    assert lines[completion + 1].strip() == EXIT_GUARD


def test_comment_fetch_failure_treated_as_no_feedback(tmp_path):
    run = WorkflowRun(tmp_path)

    result = run.run(prompt_script(), fail_comments=[7])
    assert result.returncode == 0, result.stderr
    assert (run.runner / "comments-7.txt").read_text() == ""
    assert "comment fetch failed; refining without feedback" in result.stderr
    assert "Reviewer comments" not in prompt_from(result)
