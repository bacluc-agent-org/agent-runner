from pathlib import Path
import os
import subprocess
import tempfile
import textwrap


ROOT = Path(__file__).parents[1]


def test_opencode_workflow_reports_safe_auth_and_dispatch_context():
    workflow = (ROOT / ".github/workflows/opencode.yml").read_text()

    assert "auth_status='valid'" in workflow
    assert "auth_status='invalid'" in workflow
    assert "auth_status='missing'" in workflow
    assert "OPENCODE_AUTH_CONTENT present=%s" in workflow
    assert "Coordinator dispatch failed: model=%s provider=%s exit_status=%s auth_validation=%s" in workflow
    assert "Credential values are not logged." in workflow


def test_selector_diagnostics_distinguish_missing_and_out_of_scope_selection():
    workflow = (ROOT / ".github/workflows/hourly-issue.yml").read_text()

    assert "emitted no valid SELECTED_ISSUE:<number> line" in workflow
    assert "chose issue %s outside the candidate set (%s candidates)" in workflow


def _step_script(workflow_path, step_name):
    lines = workflow_path.read_text().splitlines()
    marker = f"- name: {step_name}"
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    run = next(index for index in range(start, len(lines)) if lines[index].strip() == "run: |") + 1
    body = []
    for line in lines[run:]:
        if line and not line.startswith("          "):
            break
        body.append(line[10:] if line else "")
    return textwrap.dedent("\n".join(body))


def _run(script, **env):
    return subprocess.run(["bash", "-c", script], env={**os.environ, **env}, text=True, capture_output=True)


def test_auth_diagnostic_executes_and_never_prints_credentials():
    script = _step_script(ROOT / ".github/workflows/opencode.yml", "Run coordinator")
    start = script.index("auth_status='not-required'", script.index("Coordinator statuses:"))
    end = script.index("\nfi\necho \"coordinator_status=", start) + len("\nfi")
    diagnostic = "if ((coordinator_status != 0)); then\n" + script[start:end]
    credential = "representative-secret-never-log"
    with tempfile.TemporaryDirectory() as home:
        auth_file = Path(home) / ".local/share/opencode/auth.json"
        auth_file.parent.mkdir(parents=True)
        outcomes = []
        for content, expected in ((None, "missing-or-invalid"), ("not json", "missing-or-invalid"), (f'{{"openai":{{"type":"oauth","access":"{credential}","refresh":"r","expires":1}}}}', "valid")):
            if content is None:
                auth_file.unlink(missing_ok=True)
            else:
                auth_file.write_text(content)
            result = _run(f"coordinator_status=1; MODEL=openai/example; {diagnostic}", HOME=home)
            assert result.returncode == 0
            assert f"auth_validation={expected}" in result.stderr
            assert credential not in result.stdout + result.stderr
            outcomes.append(result.stderr)
        assert len(outcomes) == 3


def test_selector_validation_executes_missing_malformed_and_valid_outputs():
    script = _step_script(ROOT / ".github/workflows/hourly-issue.yml", "Pick issue and dispatch")
    start = script.index('issue_number="$(grep -E')
    end = script.index("\nprintf 'issue_number=%s", start)
    validation = script[start:end]
    with tempfile.TemporaryDirectory() as directory:
        candidates = Path(directory) / "candidates"
        candidates.write_text("41\n42\n")
        output = Path(directory) / "output"
        outcomes = (("", "no valid SELECTED_ISSUE"), ("SELECTED_ISSUE: nope\n", "no valid SELECTED_ISSUE"), ("SELECTED_ISSUE: 99\n", "outside the candidate set"))
        for selection, expected in outcomes:
            result = _run(f"candidates={candidates}; selection={directory}/selection; printf '%s' \"$SELECTION\" > \"$selection\"; GITHUB_OUTPUT={output}; {validation}", SELECTION=selection)
            assert result.returncode != 0
            assert expected in result.stderr
        result = _run(f"candidates={candidates}; selection={directory}/selection; printf '%s' \"$SELECTION\" > \"$selection\"; GITHUB_OUTPUT={output}; {validation}; printf 'issue_number=%s\\n' \"$issue_number\" >> \"$GITHUB_OUTPUT\"", SELECTION="SELECTED_ISSUE: 42")
        assert result.returncode == 0
        assert "issue_number=42" in output.read_text()
