import os
from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/opencode.yml"
RUN_ID = "123456789"
ISSUE = "449"
BRANCH = f"agent-run/{ISSUE}-{RUN_ID}"


def extract_step_run(path, step_id="push-work"):
    lines = path.read_text().splitlines()
    id_index = next(i for i, line in enumerate(lines) if line.strip() == f"id: {step_id}")
    step_start = next(
        i for i in range(id_index, -1, -1) if lines[i].lstrip().startswith("- ")
    )
    run_index = next(
        i for i in range(step_start, len(lines)) if lines[i].lstrip().startswith("run: |")
    )
    run_indent = len(lines[run_index]) - len(lines[run_index].lstrip())
    body = []
    for line in lines[run_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= run_indent:
            break
        body.append(line)
    script = textwrap.dedent("\n".join(body)).strip() + "\n"
    assert "PUSHED_REPOS" in script
    assert "BRANCH_NAME" in script
    return script


def parse_output(path):
    output = {}
    lines = path.read_text().splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if "<<" in line and not line.startswith(" "):
            key, delimiter = line.split("<<", 1)
            value = []
            index += 1
            while index < len(lines) and lines[index] != delimiter:
                value.append(lines[index])
                index += 1
            output[key] = "\n".join(value)
        elif "=" in line:
            key, value = line.split("=", 1)
            output[key] = value
        index += 1
    return output


def git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def prepare_workspace(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    origin = tmp_path / "github.com" / "example" / "demo.git"
    origin.parent.mkdir(parents=True)
    git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    git("init", "-b", "main", cwd=workspace)
    (workspace / "seed.txt").write_text("seed\n")
    git("add", "seed.txt", cwd=workspace)
    git(
        "-c",
        "user.name=Seed",
        "-c",
        "user.email=seed@example.com",
        "commit",
        "-m",
        "seed",
        cwd=workspace,
    )
    git("remote", "add", "origin", str(origin), cwd=workspace)
    output = tmp_path / "github_output"
    output.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "XDG_CONFIG_HOME": str(home / ".config"),
        "GIT_TERMINAL_PROMPT": "0",
        "GITHUB_WORKSPACE": str(workspace),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_RUN_ID": RUN_ID,
        "ISSUE_NUMBER": ISSUE,
        "GITHUB_TOKEN": "test-token",
    }
    return workspace, origin, home, output, env


def run_step(script, env):
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        cwd=env["GITHUB_WORKSPACE"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_push_step_commits_with_local_identity(tmp_path):
    workspace, origin, home, output, env = prepare_workspace(tmp_path)
    (workspace / "change.txt").write_text("change\n")

    result = run_step(extract_step_run(WORKFLOW), env)

    assert result.returncode == 0, result.stderr
    parsed = parse_output(output)
    assert parsed["branch_name"] == BRANCH
    assert f"{workspace} ({BRANCH})" in parsed["pushed_repos"]
    assert parsed["pushed_slugs"].strip() == "example/demo"
    author = git("-C", str(workspace), "log", "-1", "--format=%an <%ae>", cwd=tmp_path).strip()
    assert author == "agent-runner <agent-runner@users.noreply.github.com>"
    assert git("-C", str(workspace), "config", "user.name", cwd=tmp_path).strip() == "agent-runner"
    assert (
        git("-C", str(workspace), "config", "user.email", cwd=tmp_path).strip()
        == "agent-runner@users.noreply.github.com"
    )
    assert not (home / ".gitconfig").exists()
    assert not (home / ".config/git/config").exists()
    git("-C", str(origin), "rev-parse", "--verify", f"refs/heads/{BRANCH}", cwd=tmp_path)


def test_push_step_skips_push_when_commit_fails(tmp_path):
    workspace, origin, home, output, env = prepare_workspace(tmp_path)
    hook = workspace / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (workspace / "change.txt").write_text("change\n")
    head = git("-C", str(workspace), "rev-parse", "HEAD", cwd=tmp_path).strip()

    result = run_step(extract_step_run(WORKFLOW), env)

    assert result.returncode == 0, result.stderr
    parsed = parse_output(output)
    assert "pushed_repos" not in parsed
    assert "pushed_slugs" not in parsed
    assert "branch_name" not in parsed
    assert "Failed to commit changes" in result.stdout + result.stderr
    assert git("-C", str(workspace), "rev-parse", "HEAD", cwd=tmp_path).strip() == head
    exists = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "--verify", f"refs/heads/{BRANCH}"],
        capture_output=True,
        text=True,
    )
    assert exists.returncode != 0
    assert not (home / ".gitconfig").exists()
    assert not (home / ".config/git/config").exists()
