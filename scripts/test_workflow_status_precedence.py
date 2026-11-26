import re
import os
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path


ROOT = Path(__file__).parents[1]
HOURLY_WORKFLOW = ROOT / ".github/workflows/hourly-issue.yml"
OPENCODE_WORKFLOW = ROOT / ".github/workflows/opencode.yml"


def test_selector_status_precedes_tee_status():
    content = HOURLY_WORKFLOW.read_text()
    block = content[content.index("selection_statuses=") : content.index("if ! grep -q", content.index("selection_statuses="))]

    assert block.index("if (( selection_status != 0 )); then") < block.index("if (( selection_tee_status != 0 )); then")
    assert re.search(r'if \(\( selection_status != 0 \)\); then.*?exit "\$selection_status"', block, re.S)
    assert 'exit "$selection_tee_status"' in block


def test_model_discovery_status_precedes_tee_status_and_falls_back():
    content = OPENCODE_WORKFLOW.read_text()
    block = content[content.index("discovery_statuses=") : content.index("else\n            model_source='requested'", content.index("discovery_statuses="))]

    command_failure = block.index("if ((discovery_status != 0)); then")
    tee_failure = block.index("elif ((discovery_tee_status != 0)); then")
    assert command_failure < tee_failure
    assert block.index('model="$fallback_model"', command_failure) < tee_failure
    assert 'discovery_status=1' not in block


def _run_fragment(fragment, command_status, tee_status, command_output=""):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        github_output = root / "github-output"
        bin_dir = root / "bin"
        bin_dir.mkdir()
        timeout = bin_dir / "timeout"
        timeout.write_text(
            "#!/usr/bin/env bash\nprintf '%s\\n' \"${FAKE_COMMAND_OUTPUT:-}\"\nexit \"$FAKE_COMMAND_STATUS\"\n"
        )
        timeout.chmod(0o755)
        tee = bin_dir / "tee"
        tee.write_text(
            "#!/usr/bin/env bash\ncat > \"${!#}\"\nexit \"$FAKE_TEE_STATUS\"\n"
        )
        tee.chmod(0o755)
        environment = dict(
            os.environ,
            PATH=f"{bin_dir}:{os.environ['PATH']}",
            FAKE_COMMAND_STATUS=str(command_status),
            FAKE_TEE_STATUS=str(tee_status),
            FAKE_COMMAND_OUTPUT=command_output,
            RUNNER_TEMP=str(root),
            GITHUB_OUTPUT=str(github_output),
        )
        result = subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", fragment],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        output_text = github_output.read_text() if github_output.exists() else ""
        return result, output_text


def test_selector_status_handling_executes_all_command_and_tee_combinations():
    content = HOURLY_WORKFLOW.read_text()
    status_start = content.index("selection_statuses=")
    start = content.rindex("set +e", 0, status_start)
    end = content.index("          if ! grep -q", start)
    fragment = content[start:end]
    for command_status, tee_status, expected in [(0, 0, 0), (124, 0, 124), (7, 0, 7), (0, 9, 9), (124, 9, 124), (7, 9, 7)]:
        result, _ = _run_fragment(
            "selection=/tmp/selection.out\nselection_model=model\nprompt=prompt\nSELECTION_TIMEOUT_MINUTES=10\n" + fragment,
            command_status,
            tee_status,
        )
        assert result.returncode == expected, result.stderr
        assert f"opencode={command_status} tee={tee_status}" in result.stdout


def test_model_discovery_status_handling_executes_all_command_and_tee_combinations():
    content = OPENCODE_WORKFLOW.read_text()
    search_start = content.index("discovery_output=")
    start = content.index("            set +e", search_start)
    end = content.index("          else\n            model_source='requested'", start)
    fragment = content[start:end]
    setup = (
        "fallback_model=fallback/model\n"
        "DISCOVERY_TIMEOUT=5\n"
        "discovery_prompt=prompt\n"
        "discovery_output=/tmp/discovery.out\n"
        "stop_token=stop\n"
        "available_models_file=/tmp/available-models.txt\n"
        "printf '%s\\n' fallback/model discovered/model > \"$available_models_file\"\n"
        "deny_re='^$'\n"
    )
    cases = [(0, 0, "discovered/model"), (124, 0, "fallback/model"), (7, 0, "fallback/model"), (0, 9, "fallback/model"), (124, 9, "fallback/model"), (7, 9, "fallback/model")]
    for command_status, tee_status, expected_model in cases:
        result, _ = _run_fragment(
            setup + fragment + "printf 'model=%s\\n' \"$model\"\n",
            command_status,
            tee_status,
            "CARRIERS: coordinator: discovered/model" if command_status == tee_status == 0 else "",
        )
        assert result.returncode == 0, result.stderr
        assert f"model={expected_model}" in result.stdout
        assert f"opencode={command_status} tee={tee_status}" in result.stdout


def test_coordinator_status_handling_executes_all_command_and_tee_combinations():
    content = OPENCODE_WORKFLOW.read_text()
    search_start = content.index("id: coordinator")
    func_start = content.index("run_coordinator() {", search_start)
    func_end = content.index("\n          }", func_start) + len("\n          }")
    call_start = content.index('run_coordinator "$COORDINATOR_TIMEOUT" "$PROMPT"', func_end)
    end = content.index('\n          if [[ -n "$COORDINATOR_PROGRESS_CHECKER" ]]; then', call_start)
    fragment = content[func_start:func_end] + "\n" + content[call_start:end]
    setup = (
        "COORDINATOR_TIMEOUT=10\n"
        "COORDINATOR_PROGRESS_CHECKER=\n"
        "stop_token=stop\n"
        "coordinator_session_title=coordinator\n"
        "AGENT=coordinator\n"
        "MODEL=model\n"
        "PROMPT=prompt\n"
    )
    cases = [(0, 0, False), (124, 0, True), (7, 0, False), (0, 9, False), (124, 9, True), (7, 9, False)]
    for command_status, tee_status, timed_out in cases:
        result, outputs = _run_fragment(setup + fragment, command_status, tee_status)
        assert result.returncode == command_status, result.stderr
        assert f"opencode={command_status} tee={tee_status}" in result.stdout
        assert f"coordinator_status={command_status}" in outputs.splitlines()
        assert f"coordinator_tee_status={tee_status}" in outputs.splitlines()
        assert ("failure_mode=timeout" in outputs.splitlines()) == timed_out


def test_run_result_classifier_reports_timeout_and_non_fatal_tee_failure():
    content = OPENCODE_WORKFLOW.read_text()
    start = content.index("          # Determine status")
    end = content.index("\n\n          # Check for existing PRs", start)
    fragment = content[start:end]
    cases = [
        ("0", "0", "", "", "✅ completed"),
        ("0", "9", "", "", "✅ completed (tee log capture failed: exit 9)"),
        ("124", "0", "", "", "⏱️ coordinator timed out (exit 124)"),
        ("124", "9", "auth", "", "⏱️ coordinator timed out (exit 124)"),
        ("7", "0", "auth", "", "❌ fatal error: auth"),
        ("7", "0", "", "", "⚠️ failed (coordinator exit 7)"),
        ("0", "0", "", "incomplete", "❌ failed (incomplete)"),
        ("0", "0", "", "completion-gate-error", "❌ failed (completion-gate-error)"),
    ]
    for coordinator_status, tee_status, fatal_reason, failure_mode, expected in cases:
        result = subprocess.run(
            [
                "bash",
                "-Eeuo",
                "pipefail",
                "-c",
                fragment + '\nprintf "%s\\n" "$STATUS"\n',
            ],
            cwd=ROOT,
            env=dict(
                os.environ,
                COORDINATOR_STATUS=coordinator_status,
                COORDINATOR_TEE_STATUS=tee_status,
                FATAL_REASON=fatal_reason,
                FAILURE_MODE=failure_mode,
            ),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


def test_classifier_reads_coordinator_step_outputs():
    content = OPENCODE_WORKFLOW.read_text()
    block = content[content.index("- name: Post run-result comment"):]
    assert "COORDINATOR_STATUS: ${{ steps.coordinator.outputs.coordinator_status }}" in block
    assert "COORDINATOR_TEE_STATUS: ${{ steps.coordinator.outputs.coordinator_tee_status }}" in block
    assert "FAILURE_MODE: ${{ steps.coordinator.outputs.failure_mode || steps.fatal-guard.outputs.failure_mode }}" in block


def test_coordinator_fences_and_stale_output_clear_are_ordered():
    content = OPENCODE_WORKFLOW.read_text()
    step = content[content.index("id: coordinator") : content.index("- name: Fatal guard")]
    for name in ("run_coordinator() {", "audit_output() {"):
        start = step.index(name)
        block = step[start : step.index("\n          }", start)]
        assert 'fence_token="$(openssl rand -hex 32)"' in block
        assert ': "${fence_token:?fence token generation failed}"' in block
        assert "printf '::stop-commands::%s\\n' \"$fence_token\"" in block
        assert "printf '::%s::\\n' \"$fence_token\"" in block
        assert "$stop_token" not in block

    tail = step.index('tail -n 120 "$RUNNER_TEMP/coordinator.out"')
    clear = step.index('rm -f "$RUNNER_TEMP/coordinator.out"', tail)
    resume = step.index('run_coordinator "$COORDINATOR_RESUME_TIMEOUT" "$resume_prompt"')
    assert tail < clear < resume
