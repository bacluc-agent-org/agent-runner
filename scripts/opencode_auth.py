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
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            stream.write(json.dumps(auth))
    finally:
        if fd != -1:
            os.close(fd)
    return dest
