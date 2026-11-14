import os
import subprocess
import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import coordinator_result

WORKFLOW = Path(__file__).parents[1] / ".github/workflows/opencode.yml"


def _install_classifier(root: Path) -> None:
    scripts = root / "agent-runner" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "coordinator_result.py").write_text(
        Path(__file__).with_name("coordinator_result.py").read_text()
    )
    (scripts / "progress-reporting.txt").write_text("test\n")


def _run(script: str, root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        env={**os.environ, "RUNNER_TEMP": str(root), **env},
        capture_output=True,
        text=True,
    )


def _run_coordinator_block() -> str:
    workflow = WORKFLOW.read_text()
    block = workflow.split(
        "          coordinator_session_title='coordinator-run'\n", 1
    )[1].split(
        "          exit 0\n", 1
    )[0]
    return textwrap.dedent(block)


def _post_result_block() -> str:
    workflow = WORKFLOW.read_text()
    block = workflow.split(
        '          RUN_URL="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"\n',
        1,
    )[1].split(
        "          # Check for existing PRs in every repo this run pushed to",
        1,
    )[0]
    return textwrap.dedent(block)


@pytest.mark.parametrize(
    ("status", "fatal", "expected"),
    [
        ("0", "", "✅ completed"),
        ("0", "balance", "✅ completed"),
        ("1", "balance", "❌ fatal error: balance"),
        ("124", "", "⚠️ failed (coordinator timeout)"),
        ("17", "", "⚠️ failed (coordinator exit 17)"),
        ("", "", "⚠️ failed (coordinator did not report an exit status)"),
    ],
)
def test_classify(status, fatal, expected):
    assert coordinator_result.classify(status, fatal) == expected


@pytest.mark.parametrize(
    ("opencode_status", "tee_status", "expected_code", "expected_output", "expect_warning"),
    [
        (0, 7, 0, "coordinator_status=0\ncoordinator_tee_status=7\n", True),
        (17, 0, 17, "coordinator_status=17\ncoordinator_tee_status=0\n", False),
        (
            124,
            0,
            124,
            "coordinator_status=124\ncoordinator_tee_status=0\nfailure_mode=timeout\n",
            False,
        ),
    ],
)
def test_run_coordinator_uses_opencode_status_not_tee(
    opencode_status, tee_status, expected_code, expected_output, expect_warning
):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        _install_classifier(root)

        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "timeout").write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        (bin_dir / "opencode").write_text(
            '#!/usr/bin/env bash\nexit "$OPENCODE_STATUS"\n'
        )
        (bin_dir / "tee").write_text(
            '#!/usr/bin/env bash\ncat >/dev/null\nexit "$TEE_STATUS"\n'
        )
        for command in ("timeout", "opencode", "tee"):
            (bin_dir / command).chmod(0o755)

        output = root / "github-output"
        result = _run(
            _run_coordinator_block(),
            root,
            {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output),
                "MODEL": "test-model",
                "AGENT": "coordinator",
                "PROMPT": "test prompt",
                "COORDINATOR_TIMEOUT": "1",
                "OPENCODE_STATUS": str(opencode_status),
                "TEE_STATUS": str(tee_status),
            },
        )

        assert result.returncode == expected_code, result.stderr
        assert output.read_text() == expected_output
        assert (
            "::warning::coordinator log capture failed (tee exit 7)" in result.stdout
        ) is expect_warning


@pytest.mark.parametrize(
    ("status", "fatal", "expected"),
    [
        ("0", "", "Run result: ✅ completed"),
        ("17", "", "Run result: ⚠️ failed (coordinator exit 17)"),
        ("", "", "Run result: ⚠️ failed (coordinator did not report an exit status)"),
        ("1", "balance", "Run result: ❌ fatal error: balance"),
    ],
)
def test_post_run_result_comment_reports_the_real_status(status, fatal, expected):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        _install_classifier(root)
        result = _run(
            _post_result_block(),
            root,
            {"COORDINATOR_STATUS": status, "FATAL_REASON": fatal},
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
