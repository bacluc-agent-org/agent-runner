"""Shared OpenAI (ChatGPT) OAuth constants and helpers.

Used by scripts/chatgpt-login.py (browser login) and scripts/refresh-token.py
(refresh-token renewal) so the OAuth parameters cannot drift between the
primary and fallback credential paths.
"""
import json
import os
from pathlib import Path

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
TOKEN_PATH = "/oauth/token"
SCOPE = "openid profile email offline_access"
USER_AGENT = "opencode/refresh"


def build_auth_json(access: str, refresh: str, expires: int) -> dict:
    if not isinstance(access, str) or not access:
        raise ValueError("access must be a non-empty string")
    if not isinstance(refresh, str) or not refresh:
        raise ValueError("refresh must be a non-empty string")
    if not isinstance(expires, int):
        raise ValueError("expires must be an int (millisecond epoch)")
    return {"openai": {"type": "oauth", "access": access, "refresh": refresh, "expires": expires}}


def write_auth_file(auth: dict, path: Path | None = None) -> Path:
    dest = path or Path.home() / ".local" / "share" / "opencode" / "auth.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(auth), encoding="utf-8")
    os.chmod(dest, 0o600)
    return dest


def write_auth_content(content: str, path: Path | None = None) -> Path:
    auth = json.loads(content)
    account = auth.get("openai") if isinstance(auth, dict) else None
    if not isinstance(account, dict) or account.get("type") != "oauth" or not all(
        isinstance(account.get(key), str) and account[key]
        for key in ("access", "refresh")
    ) or not isinstance(account.get("expires"), int):
        raise ValueError("auth content is not valid OpenCode OAuth JSON")
    return write_auth_file(auth, path)
