import os
import re
import subprocess
import textwrap


WORKFLOW = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "opencode.yml")


def watchdog_source():
    workflow = open(WORKFLOW, encoding="utf-8").read()
    match = re.search(r'cat > "\$watchdog_script" <<\'WATCHDOG\'\n(.*?)\n\s+WATCHDOG', workflow, re.S)
    assert match
    source = textwrap.dedent(match.group(1)) + "\n"
    return source.replace("total_seconds=$((total_minutes * 60))", "total_seconds=$((total_minutes))").replace("idle_seconds=$((idle_minutes * 60))", "idle_seconds=$((idle_minutes))")


def run_watchdog(tmp_path, mode, total=10, idle=2):
    fake = tmp_path / "opencode"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "printf '%s\\n' $$ > \"$FAKE_PID_FILE\"\n"
        "case \"$FAKE_MODE\" in\n"
        " silent) while :; do :; done ;;\n"
        " periodic) for i in {1..4}; do printf 'progress %s\\n' \"$i\"; sleep 1; done ;;\n"
        " storage) mkdir -p \"$XDG_DATA_HOME/opencode/storage/session/project\" \"$XDG_DATA_HOME/opencode/storage/message/session-id\"; printf '{\"title\":\"%s\"}\\n' \"$OPENCODE_WATCHDOG_TITLE\" > \"$XDG_DATA_HOME/opencode/storage/session/project/session-id.json\"; for i in {1..4}; do printf '%s\\n' \"$i\" > \"$XDG_DATA_HOME/opencode/storage/message/session-id/$i.json\"; sleep 1; done ;;\n"
        " child_124) exit 124 ;;\n"
        " active) (sleep 30) & printf '%s\\n' $! > \"$FAKE_CHILD_FILE\"; wait ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    script = tmp_path / "watchdog.sh"
    script.write_text(watchdog_source(), encoding="utf-8")
    output = tmp_path / "output"
    status = tmp_path / "status"
    tee_status = tmp_path / "tee-status"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "FAKE_MODE": mode, "FAKE_PID_FILE": str(tmp_path / "pid"), "FAKE_CHILD_FILE": str(tmp_path / "child"), "XDG_DATA_HOME": str(tmp_path / "data")}
    if mode == "tee_failure":
        (tmp_path / "tee").write_text("#!/usr/bin/env bash\ncat >/dev/null\nexit 7\n", encoding="utf-8")
        (tmp_path / "tee").chmod(0o755)
    process = subprocess.run(
        ["setsid", "bash", str(script), str(output), str(output), str(total), str(idle), str(status), str(tee_status), "coordinator", "model/test", "test", "prompt"],
        env=env, capture_output=True, text=True, timeout=8,
    )
    return process, status, tee_status


def test_silent_output_reports_zero_progress_and_cleans_group(tmp_path):
    process, status, _ = run_watchdog(tmp_path, "silent")
    assert process.returncode == 124
    assert status.read_text().strip() == "124"
    assert "zero-progress" in process.stderr
    assert not _alive(int((tmp_path / "pid").read_text()))


def test_periodic_output_completes_normally(tmp_path):
    process, status, tee_status = run_watchdog(tmp_path, "periodic")
    assert process.returncode == 0
    assert status.read_text().strip() == "0"
    assert tee_status.read_text().strip() == "0"
    assert "progress 4" in (tmp_path / "output").read_text()


def test_active_descendant_defers_idle_timeout(tmp_path):
    process, status, _ = run_watchdog(tmp_path, "active", total=2, idle=1)
    assert process.returncode == 124
    assert "total timeout" in process.stderr
    assert status.read_text().strip() == "124"
    assert not _alive(int((tmp_path / "child").read_text()))


def test_total_timeout_is_distinct_from_idle_timeout(tmp_path):
    process, status, _ = run_watchdog(tmp_path, "silent", total=1, idle=10)
    assert process.returncode == 124
    assert "total timeout" in process.stderr
    assert "zero-progress" not in process.stderr
    assert status.read_text().strip() == "124"


def test_storage_activity_defers_idle_timeout(tmp_path):
    process, status, _ = run_watchdog(tmp_path, "storage", total=2, idle=1)
    assert process.returncode == 124
    assert "total timeout" in process.stderr
    assert "zero-progress" not in process.stderr
    assert status.read_text().strip() == "124"


def test_child_exit_124_is_not_timeout(tmp_path):
    process, status, _ = run_watchdog(tmp_path, "child_124")
    assert process.returncode == 0
    assert status.read_text().strip() == "124"
    assert not (tmp_path / "coordinator-failure-mode").exists()


def test_tee_failure_is_reported(tmp_path):
    process, status, tee_status = run_watchdog(tmp_path, "tee_failure")
    assert process.returncode == 0
    assert status.read_text().strip() == "0"
    assert tee_status.read_text().strip() == "7"


def test_decimal_timeout_is_rejected(tmp_path):
    process, _, _ = run_watchdog(tmp_path, "periodic", total="0.5")
    assert process.returncode == 2
    assert "positive integers" in process.stderr


def test_invalid_timeout_is_rejected(tmp_path):
    process, _, _ = run_watchdog(tmp_path, "periodic", idle="nope")
    assert process.returncode == 2
    assert "positive integers" in process.stderr


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
