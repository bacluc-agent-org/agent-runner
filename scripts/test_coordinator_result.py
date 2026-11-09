import coordinator_result
import os
import subprocess
import textwrap
from tempfile import TemporaryDirectory
from pathlib import Path


WORKFLOW = Path(__file__).parents[1] / ".github/workflows/opencode.yml"


def test_success_ignores_tee_failure():
    assert coordinator_result.classify(0, 1) == ("✅ completed", "success")


def test_timeout_is_classified_from_coordinator_status():
    assert coordinator_result.classify(124, 1) == ("⚠️ failed (coordinator timeout)", "timeout")


def test_other_nonzero_is_detected():
    assert coordinator_result.classify(17, 1) == ("⚠️ failed (coordinator exit 17)", "error")


def test_fatal_reason_takes_precedence():
    assert coordinator_result.classify(0, 1, "balance") == ("❌ fatal error: balance", "error")


def test_workflow_uses_numeric_coordinator_outputs():
    workflow = WORKFLOW.read_text()
    assert 'exit 0' in workflow
    assert 'exit "$coordinator_tee_status"' not in workflow
    assert 'COORDINATOR_STATUS: ${{ steps.coordinator.outputs.coordinator_status || \'1\' }}' in workflow
    assert "COORDINATOR_OUTCOME" not in workflow
    assert 'coordinator_status != 0' in workflow


def test_workflow_preserves_coordinator_status_and_exit_code():
    workflow = WORKFLOW.read_text()
    block = textwrap.dedent(
        workflow.split("          coordinator_session_title='coordinator-run'\n", 1)[1].split(
            "          exit 0\n", 1
        )[0]
    )

    with TemporaryDirectory() as directory:
        root = Path(directory)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "timeout").write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        (bin_dir / "opencode").write_text('#!/usr/bin/env bash\nexit "$OPENCODE_STATUS"\n')
        (bin_dir / "tee").write_text(
            '#!/usr/bin/env bash\ncat >/dev/null\nexit "$TEE_STATUS"\n'
        )
        for command in ("timeout", "opencode", "tee"):
            (bin_dir / command).chmod(0o755)
        runner_temp = root / "runner-temp"
        script_dir = runner_temp / "agent-runner" / "scripts"
        script_dir.mkdir(parents=True)
        (script_dir / "coordinator_result.py").write_text(
            (Path(__file__).with_name("coordinator_result.py")).read_text()
        )
        script = root / "coordinator.sh"
        script.write_text(block + "\n")
        script.chmod(0o755)

        def run(coordinator_status, tee_status):
            output = root / f"output-{coordinator_status}-{tee_status}"
            environment = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(runner_temp),
                "GITHUB_OUTPUT": str(output),
                "MODEL": "test-model",
                "AGENT": "coordinator",
                "PROMPT": "test prompt",
                "COORDINATOR_TIMEOUT": "1",
                "OPENCODE_STATUS": str(coordinator_status),
                "TEE_STATUS": str(tee_status),
            }
            result = subprocess.run(["bash", str(script)], env=environment, capture_output=True, text=True)
            return result.returncode, output.read_text()

        assert run(0, 7) == (0, "coordinator_status=0\ncoordinator_tee_status=7\nfailure_mode=success\n")
        assert run(124, 0)[0] == 124
        assert run(17, 0)[0] == 17
