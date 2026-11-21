import base64
import json

from aisix_oauth import account_id, model_slugs, write_config


def oauth_token(identifier="account-123"):
    payload = {"https://api.openai.com/auth": {"chatgpt_account_id": identifier}}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_account_id_reads_chatgpt_account_claim():
    assert account_id(oauth_token()) == "account-123"


def test_model_slugs_uses_configured_supported_names(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_SLUGS", "gpt-5.6-luna\ninvalid/name\ngpt-4o-mini")

    assert model_slugs("unused") == ["gpt-4o-mini", "gpt-5.6-luna"]


def test_write_config_materializes_oauth_resources(monkeypatch, tmp_path):
    token = oauth_token()
    monkeypatch.setenv("OPENAI_ACCESS_TOKEN", token)
    monkeypatch.setenv("AISIX_CALLER_KEY", "caller-key")
    monkeypatch.setenv("OPENAI_MODEL_SLUGS", "gpt-5.6-luna\ngpt-4o-mini")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "run-123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    resources_path, config_path = write_config()

    resources = resources_path.read_text(encoding="utf-8")
    assert resources_path.stat().st_mode & 0o777 == 0o600
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert f"api_key: {token}" in resources
    assert "ChatGPT-Account-Id: account-123" in resources
    assert "model_name: \"gpt-5.6-luna\"" in resources
    assert "model_name: \"gpt-4o-mini\"" in resources
    assert "session-id: \"run-123-2\"" in resources
    assert config_path.read_text(encoding="utf-8") == (
        "resources_file: /etc/aisix/resources.yaml\n"
        "proxy:\n"
        "  addr: 0.0.0.0:3000\n"
        "admin:\n"
        "  enabled: false\n"
    )
