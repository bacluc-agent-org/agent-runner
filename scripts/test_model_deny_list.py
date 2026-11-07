"""Validate the invariants of model-deny-list.txt.

hourly-issue.yml, opencode.yml and refine-issues.yml feed this file to
`grep -Ev -f`, so every line is a live extended regex: a blank line would
match every model and deny everything, an invalid pattern would make grep
itself fail at runtime, and a pipe in a comment would create alternation
that could deny a valid model.
"""

import re
from pathlib import Path

DENY_LIST = Path(__file__).parent / "model-deny-list.txt"


def read_lines():
    return DENY_LIST.read_text().splitlines()


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
