import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/opencode.yml"


def test_generated_and_transient_paths_are_ignored():
    for path in (
        ".prettierignore.completion-check",
        ".opencode/node_modules/example/index.js",
        ".opencode/package-lock.json",
    ):
        result = subprocess.run(["git", "check-ignore", "--no-index", "-q", path], cwd=ROOT)
        assert result.returncode == 0, path


def _git_add_line():
    return next(line.strip() for line in WORKFLOW.read_text().splitlines() if "git add -A" in line)


def test_git_add_survives_a_vanished_file():
    line = _git_add_line()
    assert "--ignore-errors" in line
    script = f"repo_dir=/tmp\ngit() {{ return 128; }}\n{line}\nprintf 'REACHED\\n'\n"
    result = subprocess.run(["bash", "-Eeuo", "pipefail", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "::warning::git add -A reported an error in /tmp" in result.stdout
    assert "REACHED" in result.stdout
