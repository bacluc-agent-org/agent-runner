import importlib.util
import json
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "refresh_token", Path(__file__).with_name("refresh-token.py")
)
assert spec and spec.loader
refresh_token = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh_token)


def test_current_refresh_token_prefers_standalone_secret(monkeypatch):
    monkeypatch.setenv("OPENAI_REFRESH_TOKEN", "standalone-refresh")
    monkeypatch.setenv("OPENCODE_AUTH_CONTENT", "not-json")
    assert refresh_token.current_refresh_token() == "standalone-refresh"


def test_refresh_outputs_tokens_without_writing_opencode_auth(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_REFRESH_TOKEN", "previous-refresh")
    monkeypatch.setattr(refresh_token.time, "time", lambda: 0)
    monkeypatch.setattr(
        refresh_token,
        "refresh_tokens",
        lambda value: {
            "access_token": "fresh-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        refresh_token,
        "write_auth_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must stay outside OpenCode")),
        raising=False,
    )

    assert refresh_token.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "access_token": "fresh-access",
        "refresh_token": "rotated-refresh",
        "expires_at": 3600000,
    }
