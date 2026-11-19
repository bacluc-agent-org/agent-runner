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
        )
        return subprocess.run(
            ["bash", "-Eeuo", "pipefail", "-c", fragment],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )


def test_selector_status_handling_executes_all_command_and_tee_combinations():
    content = HOURLY_WORKFLOW.read_text()
    status_start = content.index("selection_statuses=")
    start = content.rindex("set +e", 0, status_start)
    end = content.index("          if ! grep -q", start)
    fragment = content[start:end]
    for command_status, tee_status, expected in [(0, 0, 0), (124, 0, 124), (7, 0, 7), (0, 9, 9)]:
        result = _run_fragment(
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
    cases = [(0, 0, "discovered/model"), (124, 0, "fallback/model"), (7, 0, "fallback/model"), (0, 9, "fallback/model")]
    for command_status, tee_status, expected_model in cases:
        result = _run_fragment(
            setup + fragment + "printf 'model=%s\\n' \"$model\"\n",
            command_status,
            tee_status,
            "CARRIERS: coordinator: discovered/model" if command_status == tee_status == 0 else "",
        )
        assert result.returncode == 0, result.stderr
        assert f"model={expected_model}" in result.stdout
        assert f"opencode={command_status} tee={tee_status}" in result.stdout
