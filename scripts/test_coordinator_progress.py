import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts/check_coordinator_progress.py"


def run_check(workspace, output, baseline, *extra):
    return subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--check",
            "--workspace",
            str(workspace),
            "--output",
            str(output),
            "--baseline",
            str(baseline),
            *extra,
        ],
        capture_output=True,
        text=True,
    )


def snapshot(workspace, baseline):
    return subprocess.run(
        [
            "python3",
            str(SCRIPT),
            "--snapshot",
            "--workspace",
            str(workspace),
            "--baseline",
            str(baseline),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git("init", cwd=workspace)
    git("config", "user.email", "test@example.com", cwd=workspace)
    git("config", "user.name", "Test", cwd=workspace)
    (workspace / "tracked.txt").write_text("before\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "initial", cwd=workspace)
    return workspace


def test_successful_zero_progress_requires_exact_no_code_change_marker(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)

    output.write_text("AGENT_RESULT: NO_CODE_CHANGE\n")
    result = run_check(workspace, output, baseline)

    assert result.returncode == 2
    assert "no-code-change" in result.stdout

    output.write_text("Result: AGENT_RESULT: NO_CODE_CHANGE\n")
    result = run_check(workspace, output, baseline)

    assert result.returncode == 1


def test_changed_repository_is_observable_delivery(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)
    (workspace / "tracked.txt").write_text("delivered\n")
    output.write_text("completed\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 0
    assert "delivery" in result.stdout


def test_successful_unchanged_run_is_incomplete(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)
    output.write_text("I could not continue\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 1
    assert "incomplete" in result.stdout


def test_nested_repository_changes_are_observable_delivery(tmp_path):
    workspace = make_repo(tmp_path)
    nested = workspace / "nested"
    nested.mkdir()
    git("init", cwd=nested)
    git("config", "user.email", "test@example.com", cwd=nested)
    git("config", "user.name", "Test", cwd=nested)
    (nested / "nested.txt").write_text("before\n")
    git("add", ".", cwd=nested)
    git("commit", "-m", "nested initial", cwd=nested)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)

    (nested / "nested.txt").write_text("after\n")
    output.write_text("completed\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 0
    assert "delivery" in result.stdout


def test_nested_repository_commit_is_observable_delivery(tmp_path):
    workspace = make_repo(tmp_path)
    nested = workspace / "nested"
    nested.mkdir()
    git("init", cwd=nested)
    git("config", "user.email", "test@example.com", cwd=nested)
    git("config", "user.name", "Test", cwd=nested)
    (nested / "nested.txt").write_text("before\n")
    git("add", ".", cwd=nested)
    git("commit", "-m", "nested initial", cwd=nested)
    git("add", "nested", cwd=workspace)
    git("commit", "-m", "track nested repository", cwd=workspace)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)

    (nested / "nested.txt").write_text("after\n")
    git("add", ".", cwd=nested)
    git("commit", "-m", "nested update", cwd=nested)
    output.write_text("completed\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 0
    assert "delivery" in result.stdout


def test_new_nested_repository_is_observable_delivery(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)

    nested = workspace / "nested"
    nested.mkdir()
    git("init", cwd=nested)
    git("config", "user.email", "test@example.com", cwd=nested)
    git("config", "user.name", "Test", cwd=nested)
    (nested / "nested.txt").write_text("delivered\n")
    git("add", ".", cwd=nested)
    git("commit", "-m", "nested initial", cwd=nested)
    output.write_text("completed\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 0
    assert "delivery" in result.stdout


def test_new_root_commit_is_observable_delivery(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)
    (workspace / "delivered.txt").write_text("delivered\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "deliver", cwd=workspace)
    output.write_text("completed\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 0
    assert "delivery" in result.stdout


def test_opencode_runtime_artifacts_are_not_observable_delivery(tmp_path):
    workspace = make_repo(tmp_path)
    opencode_dir = workspace / ".opencode"
    opencode_dir.mkdir()
    package_json = opencode_dir / "package.json"
    package_lock = opencode_dir / "package-lock.json"
    package_json.write_text('{"name": "before"}\n')
    package_lock.write_text('{"lock": "before"}\n')
    git("add", ".", cwd=workspace)
    git("commit", "-m", "track opencode package", cwd=workspace)

    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)
    output.write_text("I made no changes and ran no tests.\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 1
    assert "incomplete" in result.stdout

    package_json.write_text('{"name": "after"}\n')

    result = run_check(workspace, output, baseline)

    assert result.returncode == 1
    assert "incomplete" in result.stdout

    package_lock.write_text('{"lock": "after"}\n')

    result = run_check(workspace, output, baseline)

    assert result.returncode == 1
    assert "incomplete" in result.stdout

    (opencode_dir / "runtime-marker.json").write_text("{}\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 0
    assert "delivery" in result.stdout


def test_refusal_verdict_overrides_apparent_delivery(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    verdict = tmp_path / "verdict.txt"
    snapshot(workspace, baseline)
    (workspace / "tracked.txt").write_text("delivered\n")
    output.write_text("completed\n")

    verdict.write_text("VERDICT: REFUSAL\n")
    result = run_check(workspace, output, baseline, "--verdict", str(verdict))
    assert result.returncode == 1
    assert "failing closed" in result.stdout.lower()

    verdict.write_text("VERDICT: OK\n")
    result = run_check(workspace, output, baseline, "--verdict", str(verdict))
    assert result.returncode == 0

    verdict.write_text("audit crashed\n")
    result = run_check(workspace, output, baseline, "--verdict", str(verdict))
    assert result.returncode == 1


def test_decorated_no_code_change_marker_is_accepted(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)

    output.write_text("\x1b[32m  AGENT_RESULT: NO_CODE_CHANGE  \x1b[0m\n")

    result = run_check(workspace, output, baseline)

    assert result.returncode == 2
    assert "no-code-change" in result.stdout


def test_decorated_verdict_is_accepted(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    verdict = tmp_path / "verdict.txt"
    snapshot(workspace, baseline)
    (workspace / "tracked.txt").write_text("delivered\n")
    output.write_text("completed\n")

    verdict.write_text("\x1b[32m VERDICT: OK \x1b[0m\n")
    result = run_check(workspace, output, baseline, "--verdict", str(verdict))
    assert result.returncode == 0

    verdict.write_text(" VERDICT: REFUSAL \n")
    result = run_check(workspace, output, baseline, "--verdict", str(verdict))
    assert result.returncode == 1


def test_refusal_verdict_with_no_code_marker_fails_closed(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    verdict = tmp_path / "verdict.txt"
    snapshot(workspace, baseline)
    output.write_text("AGENT_RESULT: NO_CODE_CHANGE\n")
    verdict.write_text("VERDICT: REFUSAL\n")

    result = run_check(workspace, output, baseline, "--verdict", str(verdict))

    assert result.returncode == 1
    assert "failing closed" in result.stdout.lower()


def test_stale_marker_in_resume_prompt_is_not_no_code_change(tmp_path):
    workspace = make_repo(tmp_path)
    baseline = tmp_path / "baseline.json"
    output = tmp_path / "coordinator.out"
    snapshot(workspace, baseline)

    output.write_text(
        "Continue the requested task: make the requested changes and verify them, "
        "or output exactly AGENT_RESULT: NO_CODE_CHANGE only when the task legitimately "
        "requires no code change. Do not claim completion without delivery or that exact result.\n"
    )

    result = run_check(workspace, output, baseline)

    assert result.returncode == 1
    assert "incomplete" in result.stdout
