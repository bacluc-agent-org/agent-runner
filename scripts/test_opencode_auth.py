import json
import os
import re
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
MATERIALIZER = ROOT / "scripts/materialize-opencode-auth.sh"
AUTH_JSON = json.dumps(
    {
        "openai": {
            "type": "oauth",
            "access": "test-access-token",
            "refresh": "test-refresh-token",
            "expires": 1234567890000,
        }
    }
)


def run_materializer(home: Path, content: str | None):
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("OPENCODE_AUTH_CONTENT", None)
    if content is not None:
        env["OPENCODE_AUTH_CONTENT"] = content
    return subprocess.run(
        ["bash", str(MATERIALIZER)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_materialized_v1_auth_is_private_and_not_logged(tmp_path):
    result = run_materializer(tmp_path, AUTH_JSON)

    auth_file = tmp_path / ".local/share/opencode/auth.json"
    assert result.returncode == 0
    assert json.loads(auth_file.read_text(encoding="utf-8")) == json.loads(AUTH_JSON)
    assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600
    assert "test-access-token" not in result.stdout + result.stderr
    assert "test-refresh-token" not in result.stdout + result.stderr


def test_missing_v1_auth_removes_any_existing_auth_file(tmp_path):
    auth_file = tmp_path / ".local/share/opencode/auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text(AUTH_JSON, encoding="utf-8")

    result = run_materializer(tmp_path, None)

    assert result.returncode == 0
    assert not auth_file.exists()


def test_invalid_v1_auth_is_not_materialized(tmp_path):
    result = run_materializer(tmp_path, '{"openai":{"type":"oauth"}}')

    assert result.returncode == 0
    assert not (tmp_path / ".local/share/opencode/auth.json").exists()


def test_only_coordinator_receives_scoped_v1_auth_input():
    setup = (ROOT / ".github/actions/setup-opencode/action.yml").read_text(
        encoding="utf-8"
    )
    opencode = (ROOT / ".github/workflows/opencode.yml").read_text(encoding="utf-8")
    hourly = (ROOT / ".github/workflows/hourly-issue.yml").read_text(
        encoding="utf-8"
    )
    refine = (ROOT / ".github/workflows/refine-issues.yml").read_text(
        encoding="utf-8"
    )

    assert "auth-json-v1: ${{ secrets.OPENCODE_AUTH_JSON_V1 }}" in opencode
    assert "OPENCODE_AUTH_CONTENT" not in opencode
    assert "OPENCODE_AUTH_CONTENT" not in hourly
    assert "OPENCODE_AUTH_CONTENT" not in refine
    assert "OPENCODE_AUTH_JSON_V1" not in hourly
    assert "OPENCODE_AUTH_JSON_V1" not in refine
    assert re.search(r"secrets\.OPENCODE_AUTH_JSON\s*}}", opencode) is None
    assert "auth-json-v1:" in setup


def test_refresh_workflow_uses_only_the_dedicated_v1_secret():
    workflow = (ROOT / ".github/workflows/refresh-chatgpt-auth.yml").read_text(
        encoding="utf-8"
    )

    assert "secrets.OPENCODE_AUTH_JSON_V1" in workflow
    assert workflow.count("gh secret set OPENCODE_AUTH_JSON_V1") == 1
    assert re.search(r"secrets\.OPENCODE_AUTH_JSON\s*}}", workflow) is None
    assert "CHATGPT_ACCESS_TOKEN" not in workflow
    assert "CHATGPT_REFRESH_TOKEN" not in workflow
