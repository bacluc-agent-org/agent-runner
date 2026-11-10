import os
import stat
import subprocess
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent


def _make_stub(tmp_path, name, exit_code):
    stub = tmp_path / name
    stub.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def test_all_checks_pass(tmp_path):
    for name in ("docker", "node", "npm", "uv"):
        _make_stub(tmp_path, name, 0)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    res = subprocess.run(
        [str(repo_root / "scripts" / "completion-check")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert lines and "ALL CHECKS PASSED" in lines[-1]


def test_failing_check_reported(tmp_path):
    _make_stub(tmp_path, "docker", 1)
    for name in ("node", "npm", "uv"):
        _make_stub(tmp_path, name, 0)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"}
    res = subprocess.run(
        [str(repo_root / "scripts" / "completion-check")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "FAILED CHECKS" in res.stdout
    assert "formatting (prettier --check .)" in res.stdout
