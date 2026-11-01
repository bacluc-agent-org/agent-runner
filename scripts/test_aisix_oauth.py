import base64
import importlib.util
import json
from pathlib import Path

import pytest

def load_helper():
    spec = importlib.util.spec_from_file_location(
        "aisix_oauth", Path(__file__).with_name("aisix_oauth.py")
    )
    assert spec and spec.loader, "AISIX OAuth proxy helper is missing"
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def token_for(account_id):
    payload = base64.urlsafe_b64encode(
        json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"


def test_write_resources_routes_oauth_through_aisix(tmp_path, monkeypatch):
    aisix_oauth = load_helper()
    token = token_for("test-account")
    monkeypatch.setenv("OPENAI_ACCESS_TOKEN", token)
    monkeypatch.setenv("OPENAI_REFRESH_TOKEN", "test-refresh")
    monkeypatch.setenv("OPENAI_MODEL_SLUGS", "gpt-6-luna")
    monkeypatch.setenv("AISIX_CALLER_KEY", "test-caller-key")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    resources_path, config_path = aisix_oauth.write_config()

    resources = resources_path.read_text()
    assert "api_base: https://chatgpt.com/backend-api/codex" in resources
    assert f"api_key: {token}" in resources
    assert "ChatGPT-Account-Id: test-account" in resources
    assert "x-codex-beta-features: remote_compaction_v2" in resources
    assert 'session-id: "12345-2"' in resources
    assert 'display_name: "gpt-6-luna"' in resources
    assert 'model_name: "gpt-6-luna"' in resources
    assert "key_env: AISIX_CALLER_KEY" in resources
    assert "resources_file: /etc/aisix/resources.yaml" in config_path.read_text()
    assert resources_path.stat().st_mode & 0o777 == 0o600


def test_account_id_rejects_tokens_without_chatgpt_account_claim():
    aisix_oauth = load_helper()
    token = base64.urlsafe_b64encode(b"{}").decode().rstrip("=")
    with pytest.raises(ValueError, match="account id"):
        aisix_oauth.account_id(f"header.{token}.signature")
