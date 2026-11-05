import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "refresh_token", Path(__file__).with_name("refresh-token.py")
)
assert spec and spec.loader
refresh_token = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh_token)


def test_refresh_ignores_home_auth_when_v1_secret_is_missing(tmp_path, monkeypatch):
    auth_file = tmp_path / ".local/share/opencode/auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text(
        '{"openai":{"type":"oauth","access":"old","refresh":"old-refresh","expires":1}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("OPENCODE_AUTH_CONTENT", raising=False)

    assert refresh_token._current_auth() is None


def test_refresh_emits_candidate_without_writing_auth_file(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv(
        "OPENCODE_AUTH_CONTENT",
        '{"openai":{"type":"oauth","access":"old","refresh":"old-refresh","expires":1}}',
    )
    monkeypatch.setattr(refresh_token.time, "time", lambda: 1000)
    monkeypatch.setattr(
        refresh_token,
        "refresh_tokens",
        lambda token: {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        },
    )

    assert refresh_token.main() == 0

    candidate = json.loads(capsys.readouterr().out)
    assert candidate == {
        "openai": {
            "type": "oauth",
            "access": "new-access",
            "refresh": "new-refresh",
            "expires": 4600000,
        }
    }
    assert not (tmp_path / ".local/share/opencode/auth.json").exists()
