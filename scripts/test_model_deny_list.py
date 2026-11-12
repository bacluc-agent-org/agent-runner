"""Validate the invariants of model-deny-list.txt.

hourly-issue.yml, opencode.yml and refine-issues.yml feed this file to
`grep -Ev -f`, so every line is a live extended regex: a blank line would
match every model and deny everything, an invalid pattern would make grep
itself fail at runtime, and a pipe in a comment would create alternation
that could deny a valid model.
"""

import re
import subprocess
from pathlib import Path

DENY_LIST = Path(__file__).parent / "model-deny-list.txt"


def read_lines():
    return DENY_LIST.read_text().splitlines()


def is_denied(model):
    result = subprocess.run(
        ["grep", "-Ev", "-f", str(DENY_LIST)],
        input=model,
        capture_output=True,
        text=True,
    )
    return result.returncode == 1


def test_no_blank_lines():
    lines = read_lines()
    assert lines
    assert all(line.strip() for line in lines)


def test_every_line_compiles_as_regex():
    for line in read_lines():
        re.compile(line)


def test_comment_lines_contain_no_pipe():
    for line in read_lines():
        if line.lstrip().startswith("#"):
            assert "|" not in line


def test_denies_every_gpt_6_luna_variant():
    for model in [
        "openai/gpt-6-luna",
        "openai/gpt-6-luna-fast",
        "openai/gpt-6-luna-preview",
        "opencode-go-openai/gpt-6-luna",
        "opencode-go-openai-2/gpt-6-luna",
    ]:
        assert is_denied(model), f"{model} must be deny-listed"


def test_keeps_gpt_5_6_luna():
    for model in [
        "openai/gpt-5.6-luna",
        "opencode-go-openai/gpt-5.6-luna",
    ]:
        assert not is_denied(model), f"{model} must be kept"


def test_denies_recursive_flint_v1_0_and_keeps_neighboring_models():
    for model in [
        "openrouter/recursive/flint-v1.0:free",
        "openrouter/recursive/flint-v1.0",
    ]:
        assert is_denied(model), f"{model} must be deny-listed"

    for model in [
        "openrouter/recursive/flint-v1.01:free",
        "openrouter/recursive/flint-v1.0-preview:free",
        "openrouter/recursive/flint-v1.1:free",
        "openrouter/other/flint-v1.0:free",
    ]:
        assert not is_denied(model), f"{model} must be kept"
