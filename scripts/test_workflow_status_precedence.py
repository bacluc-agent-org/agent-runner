import re
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
