#!/usr/bin/env python3
"""Renew the opencode OpenAI (ChatGPT) OAuth credential via the refresh token.

No browser needed: POST grant_type=refresh_token to auth.openai.com/oauth/token
and write the rotated tokens to ~/.local/share/opencode/auth.json.

The current credential is read from OPENCODE_AUTH_CONTENT (the workflow passes
the OPENCODE_AUTH_JSON secret) or from the auth.json file.

Exit codes: 0 success, 1 refresh failed (caller should fall back to a full
browser login).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from opencode_auth import (
    CLIENT_ID,
    ISSUER,
    SCOPE,
    TOKEN_PATH,
    USER_AGENT,
    build_auth_json,
    write_auth_file,
)


def _current_auth() -> dict | None:
    content = os.environ.get("OPENCODE_AUTH_CONTENT")
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    path = Path.home() / ".local" / "share" / "opencode" / "auth.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def refresh_tokens(refresh_token: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "scope": SCOPE,
    }).encode()
    req = urllib.request.Request(
        f"{ISSUER}{TOKEN_PATH}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {error_body}") from e


def main() -> int:
    auth = _current_auth()
    if not auth or not isinstance(auth.get("openai"), dict):
        print("No current OpenAI credential found (OPENCODE_AUTH_CONTENT or auth.json)", file=sys.stderr)
        return 1
    refresh = auth["openai"].get("refresh")
    if not refresh:
        print("No refresh token in current credential", file=sys.stderr)
        return 1

    last_error = None
    for attempt in range(2):
        try:
            tokens = refresh_tokens(refresh)
            break
        except Exception as e:
            last_error = e
            if attempt == 0:
                print(f"Token refresh attempt {attempt + 1} failed: {e}; retrying...", file=sys.stderr)
            else:
                print(f"Token refresh attempt {attempt + 1} failed: {e}", file=sys.stderr)
    else:
        print(f"Token refresh failed after retries: {last_error}", file=sys.stderr)
        return 1

    access = tokens.get("access_token", "")
    new_refresh = tokens.get("refresh_token", "")
    # If OpenAI does not rotate the refresh token, keep the existing one
    # so the credential remains valid for future refreshes.
    refresh_token_to_use = new_refresh if new_refresh else refresh
    expires_in = tokens.get("expires_in")
    if expires_in is None:
        expires_in = 3600
    try:
        expires_in = int(expires_in)
    except (ValueError, TypeError):
        expires_in = 3600
    if not access:
        print("Token refresh returned no access token", file=sys.stderr)
        return 1
    if not refresh_token_to_use:
        print("Token refresh returned no refresh token and none was preserved", file=sys.stderr)
        return 1

    try:
        auth = build_auth_json(access, refresh_token_to_use, int(time.time() * 1000) + int(expires_in) * 1000)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    write_auth_file(auth)
    json.dump(auth, sys.stdout)
    sys.stdout.write("\n")
    return 0


def _self_check() -> None:
    # Minimal self-check: verify refresh request includes scope so tokens
    # are not truncated to ~5 min lifetime.
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": "test",
        "client_id": CLIENT_ID,
        "scope": SCOPE,
    })
    assert "scope=openid+profile+email+offline_access" in body, "scope missing from refresh body"


if __name__ == "__main__":
    _self_check()
    raise SystemExit(main())
