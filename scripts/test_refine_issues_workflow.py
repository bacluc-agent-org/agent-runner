import json
import os
import re
import shlex
import subprocess
from pathlib import Path

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/refine-issues.yml"
CANDIDATE_START = 'candidates="$RUNNER_TEMP/candidate-numbers.txt"'
CANDIDATE_END = 'mv "$RUNNER_TEMP/dedup-candidates.txt" "$candidates"'
SEARCH_START = 'if ! gh api "search/issues?q=repo:${ISSUE_REPOSITORY}+is:open+is:issue+-label:agent-refined'

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
      --paginate|--slurp) shift ;;
      -*) shift ;;
      *) url="$1"; shift ;;
    esac
  done
  if [[ -n "${GH_FAIL_SEARCH:-}" && "$url" == *"search/issues"* ]]; then
    printf 'search failed\n' >&2
    exit 1
  fi
  if [[ "$url" == *"search/issues"* ]]; then
    if [[ "$url" == *"sort:created-asc"* ]]; then
      fixture="$GH_FIXTURES/unrefined-search.json"
    else
      fixture="$GH_FIXTURES/refined-search.json"
    fi
  elif [[ "$url" =~ /issues/([0-9]+)/comments ]]; then
    fixture="$GH_FIXTURES/comments-${BASH_REMATCH[1]}.json"
  elif [[ "$url" =~ /issues/([0-9]+)/events ]]; then
    fixture="$GH_FIXTURES/events-${BASH_REMATCH[1]}.json"
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


def candidate_block():
    return extract_block(CANDIDATE_START, CANDIDATE_END)


def marker_strip_block():
    return extract_if_block("prior_refinement=false")


def success_block():
    return extract_if_block("if (( validation_status == 0 )); then")


def search_block():
    return extract_if_block(SEARCH_START)


class WorkflowRun:
    def __init__(self, tmp_path):
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
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GH_FIXTURES": str(self.fixtures),
            "GH_LOG": str(tmp_path / "gh.log"),
            "RUNNER_TEMP": str(self.runner),
            "ISSUE_REPOSITORY": "bacluc-agent/agent-todo",
        }

    def write_json(self, name, value):
        (self.fixtures / name).write_text(json.dumps(value))

    def searches(self, unrefined=(), refined=()):
        self.write_json("unrefined-search.json", {"items": [{"number": n} for n in unrefined]})
        self.write_json("refined-search.json", {"items": [{"number": n} for n in refined]})

    def comments(self, number, comments):
        self.write_json(f"comments-{number}.json", [comments])

    def events(self, number, events):
        self.write_json(f"events-{number}.json", [events])

    def issue(self, number, body="", updated_at="2026-11-08T10:00:00Z"):
        self.write_json(f"issue-{number}.json", {"body": body, "updatedAt": updated_at})

    def run(self, script, fail_search=False):
        env = dict(self.env)
        if fail_search:
            env["GH_FAIL_SEARCH"] = "1"
        return subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", script],
            capture_output=True,
            text=True,
            env=env,
        )

    def candidates(self):
        path = self.runner / "candidate-numbers.txt"
        return [line for line in path.read_text().splitlines() if line]


def comment(login, created_at, body, user_type="User"):
    return {
        "user": {"login": login, "type": user_type},
        "created_at": created_at,
        "body": body,
    }


def test_marker_is_appended_only_after_validation_succeeds(tmp_path):
    run = WorkflowRun(tmp_path)
    block = success_block()
    out = tmp_path / "out.txt"

    def run_success(validation_status):
        out.write_text("refined body\n")
        script = f"""
number=7
out={shlex.quote(str(out))}
refined=0
failed=0
validation_status={validation_status}
refined_marker_timestamp='2026-11-08T10:57:18Z'
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
    assert "<!-- agent-refined:" not in out.read_text()

    result = run_success(0)
    assert result.returncode == 0
    assert "refined=1 failed=0" in result.stdout
    assert "<!-- agent-refined: 2026-11-08T10:57:18Z -->" in out.read_text()


def test_marker_line_is_stripped_before_the_prompt(tmp_path):
    run = WorkflowRun(tmp_path)
    block = marker_strip_block()

    marked = tmp_path / "marked-body.txt"
    marked.write_text("## Goal\nDo the thing\n\n<!-- agent-refined: 2026-11-08T10:00:00Z -->")
    script = f"""
body="$(cat {shlex.quote(str(marked))})"
{block}
printf 'BEGIN\\n%s\\nEND\\n' "$body"
printf 'PRIOR=%s\\n' "$prior_refinement"
"""
    result = run.run(script)
    assert result.returncode == 0
    body = result.stdout.split("BEGIN\n", 1)[1].split("\nEND\n", 1)[0]
    assert body == "## Goal\nDo the thing"
    assert "agent-refined" not in body
    assert "PRIOR=true" in result.stdout

    plain = tmp_path / "plain-body.txt"
    original = "## Goal\nDo the thing\n\n## How to implement\nStep one"
    plain.write_text(original)
    script = f"""
body="$(cat {shlex.quote(str(plain))})"
{block}
printf 'BEGIN\\n%s\\nEND\\n' "$body"
printf 'PRIOR=%s\\n' "$prior_refinement"
"""
    result = run.run(script)
    assert result.returncode == 0
    body = result.stdout.split("BEGIN\n", 1)[1].split("\nEND\n", 1)[0]
    assert body == original
    assert "PRIOR=false" in result.stdout


def test_refined_issue_needs_a_newer_human_comment(tmp_path):
    run = WorkflowRun(tmp_path)
    run.searches(refined=[2])
    run.issue(2, body="## Goal\nOld\n\n<!-- agent-refined: 2026-11-08T10:00:00Z -->")
    run.comments(2, [comment("BacLuc", "2026-11-08T09:00:00Z", "old feedback")])

    result = run.run(candidate_block())
    assert result.returncode == 0, result.stderr
    assert run.candidates() == []

    run.comments(
        2,
        [
            comment("BacLuc", "2026-11-08T09:00:00Z", "old feedback"),
            comment("BacLuc", "2026-11-08T11:00:00Z", "new feedback"),
        ],
    )
    result = run.run(candidate_block())
    assert result.returncode == 0, result.stderr
    assert run.candidates() == ["2"]
    kept = json.loads((run.runner / "comments-2.json").read_text())
    assert [item["body"] for item in kept] == ["new feedback"]


def test_bot_and_progress_comments_never_qualify(tmp_path):
    run = WorkflowRun(tmp_path)
    run.searches(refined=[2])
    run.issue(2, body="## Goal\nOld\n\n<!-- agent-refined: 2026-11-08T10:00:00Z -->")
    run.comments(
        2,
        [
            comment("some-bot", "2026-11-08T11:00:00Z", "bot feedback", user_type="Bot"),
            comment("bacluc-agent", "2026-11-08T12:00:00Z", "<!-- agent-progress -->\nstatus"),
            comment("BacLuc", "2026-11-08T09:00:00Z", "old human feedback"),
        ],
    )

    result = run.run(candidate_block())
    assert result.returncode == 0, result.stderr
    assert run.candidates() == []
    kept = json.loads((run.runner / "comments-2.json").read_text())
    assert [item["user"]["login"] for item in kept] == ["BacLuc"]


def test_searches_merge_and_deduplicate_candidates(tmp_path):
    run = WorkflowRun(tmp_path)
    run.searches(unrefined=[1, 2], refined=[2, 3])
    run.comments(1, [])
    run.comments(2, [])
    run.issue(3, body="## Goal\nOld\n\n<!-- agent-refined: 2026-11-08T10:00:00Z -->")
    run.comments(3, [comment("BacLuc", "2026-11-08T11:00:00Z", "new feedback")])

    result = run.run(candidate_block())
    assert result.returncode == 0, result.stderr
    assert run.candidates() == ["1", "2", "3"]


def test_markerless_refined_issue_uses_the_label_event_fallback(tmp_path):
    run = WorkflowRun(tmp_path)
    run.searches(refined=[2])
    run.issue(2, body="## Goal\nOld body without a marker", updated_at="2026-11-09T12:00:00Z")
    run.comments(2, [comment("BacLuc", "2026-11-09T12:00:00Z", "new feedback")])
    run.events(
        2,
        [
            {
                "event": "labeled",
                "label": {"name": "agent-refined"},
                "created_at": "2026-11-08T10:57:18Z",
            }
        ],
    )

    result = run.run(candidate_block())
    assert result.returncode == 0, result.stderr
    assert run.candidates() == ["2"]


def test_search_failure_fails_the_step_before_the_guard(tmp_path):
    run = WorkflowRun(tmp_path)
    run.searches()
    script = f"""
unrefined_json="$RUNNER_TEMP/unrefined-candidates.json"
{search_block()}
printf 'GUARD-REACHED\\n'
"""
    result = run.run(script, fail_search=True)
    assert result.returncode == 1
    assert "unrefined search failed" in result.stderr
    assert "GUARD-REACHED" not in result.stdout


def test_marker_timestamp_is_snapshotted_before_collection():
    content = WORKFLOW.read_text()
    assignment = 'refined_marker_timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"'
    assert assignment in content
    assert content.index(assignment) < content.index(CANDIDATE_START)
    assert '"$refined_marker_timestamp"' in content
    assert '<!-- agent-refined: %s -->' in content
