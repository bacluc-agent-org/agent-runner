#!/usr/bin/env python3
"""Refresh the opencode OpenAI (ChatGPT) OAuth credential.

Drives the browser through opencode's own OAuth flow:
  1. Generate PKCE verifier/challenge + state
  2. Start a localhost callback server on port 1455
  3. Open https://auth.openai.com/oauth/authorize (the same URL opencode uses)
  4. Automate login: email -> password -> 2FA -> consent
  5. Exchange the authorization code for tokens
  6. Write ~/.local/share/opencode/auth.json and print the auth JSON

Subcommands (agent-usable primitives):
  start   Launch Chrome with a CDP port, open the authorize URL, and wait for
          the OAuth callback. Prints the auth JSON and exits 0 on success.
  status  Report the current login step (email/password/2fa/consent/...).
  step    Perform one automation step (fill email/password/2FA/consent).
  stop    Close the active browser session and clear its state.

Fallback protocol (when automation is stuck):
  start & -> status -> step -> repeat
  STUCK -> playwright-cli attach --cdp=http://localhost:9222
           -> snapshot/fill/click -> detach -> step
  or steer manually until the callback fires -> start exchanges and exits 0.
  Sessions are sequential: only one start may run at a time.
  Abort = scripts/chatgpt-login.py stop.

Exit codes: 0 success, 1 setup/step failure, 2 blocked (captcha/device check).
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import signal
import socket
import sys
import threading
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

CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
AUTHORIZE_PATH = "/oauth/authorize"
CALLBACK_PATH = "/auth/callback"

ISSUER_HOST = urllib.parse.urlparse(ISSUER).netloc

STEP_BY_PATH = {
    "/log-in-or-create-account": "email",
    "/log-in": "email",
    "/log-in/password": "password",
    "/mfa-challenge": "2fa",
    "/choose-an-account": "choose-account",
    "/sign-in-with-chatgpt": "consent",
}
_STOP_REQUESTED = False

OTP_SELECTORS = [
    'input[name="otp"]',
    'input[inputmode="numeric"]',
    'input[autocomplete="one-time-code"]',
    'input[type="tel"]',
    'input[name="code"]',
    'input[data-testid*="otp"]',
    'input[data-testid*="code"]',
]

TWO_FA_TEXTS = ["Two-factor", "Two-factor authentication", "authenticator"]

def _is_headless() -> bool:
    return os.environ.get("CHATGPT_HEADLESS", "").lower() in ("1", "true")


_DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.7103.92 Safari/537.36"
_DESKTOP_VIEWPORT = {"width": 1440, "height": 900}


def _launch_browser(p, headless: bool, cdp_port: int | None = None):
    args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
    if cdp_port is not None:
        args.append(f"--remote-debugging-port={cdp_port}")
    try:
        return p.chromium.launch(headless=headless, channel="chrome", args=args)
    except Exception:
        return p.chromium.launch(headless=headless, args=args)


def _log_step(page, name: str) -> None:
    try:
        url = urllib.parse.urlsplit(page.url)
        print(f"[chatgpt-login] step={name} url={url.scheme}://{url.netloc}{url.path} title={_redact_debug_text(page.title())}", file=sys.stderr)
    except Exception:
        print(f"[chatgpt-login] step={name} url=<unknown> title=<unknown>", file=sys.stderr)


def _dump_page_state(page) -> None:
    try:
        url = urllib.parse.urlsplit(page.url)
        print(f"[chatgpt-login] failure url={url.scheme}://{url.netloc}{url.path} title={_redact_debug_text(page.title())}", file=sys.stderr)
    except Exception:
        print("[chatgpt-login] failure url=<unknown> title=<unknown>", file=sys.stderr)
    try:
        text = " ".join(page.inner_text("body").split())
        print(f"[chatgpt-login] body_text={_redact_debug_text(text)[:600]!r}", file=sys.stderr)
    except Exception:
        print("[chatgpt-login] body_text=<unavailable>", file=sys.stderr)


def _has_blocking_screen(page) -> str | None:
    # The OpenAI SPA error page ("Oops, an error occurred!") is terminal for
    # automation: report it through the same blocking channel as the bot
    # challenges, so status/step and the workflow loop handle it the same way.
    # ponytail: terminal, no reload-recovery; add a page.reload() retry at the
    # email step if the error page turns out to be transient in practice.
    if _is_error_page(page):
        return "openai error page detected"
    checks = [
        ("Verify you are human", 'text="Verify you are human"'),
        ("captcha iframe", 'iframe[src*="captcha"]'),
        ("Checking your browser", 'text="Checking your browser"'),
        ("Verify your email", 'text="Verify your email"'),
        ("Check your email", 'text="Check your email"'),
        ("human verification", 'text="human verification"'),
        ("device verification", 'text="device verification"'),
    ]
    content = page.content().lower()
    for marker in ("captcha", "human verification", "device verification"):
        if marker in content:
            return "captcha/device verification detected"
    for marker in (
        "just a moment",
        "performing security verification",
        "verifies you are not a bot",
        "ray id",
    ):
        if marker in content:
            return f"cloudflare challenge detected: {marker}"
    for label, sel in checks:
        try:
            if page.locator(sel).first.is_visible(timeout=1000):
                return label
        except Exception:
            continue
    return None


def _wait_for_blocking_clear(page, timeout_s=90) -> str | None:
    deadline = time.monotonic() + timeout_s
    while True:
        if _STOP_REQUESTED:
            return "stopped"
        try:
            blocking = _has_blocking_screen(page)
        except Exception:
            blocking = "challenge in progress"
        if blocking is None:
            return None
        if time.monotonic() >= deadline:
            return blocking
        time.sleep(5)


def _click_first_visible(page, selectors: list[str]) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.click()
                return True
        except Exception:
            continue
    return False


def _fill_first_visible(page, selectors: list[str], value: str, delay_ms: int | None = None) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                if delay_ms is None:
                    loc.fill(value)
                else:
                    loc.fill("")
                    loc.press_sequentially(value, delay=delay_ms)
                return True
        except Exception:
            continue
    return False


def _is_error_page(page) -> bool:
    try:
        title = page.title()
    except Exception:
        title = ""
    try:
        body = page.inner_text("body")
    except Exception:
        body = ""
    return "Oops, an error occurred" in title or "Oops, an error occurred" in body or "Route Error" in body


def _page_path(page) -> str:
    try:
        return urllib.parse.urlparse(page.url).path
    except Exception:
        return ""


def _step_for_path(path: str) -> str:
    return STEP_BY_PATH.get(path) or next(
        (step for route, step in STEP_BY_PATH.items() if path.startswith(f"{route}/")),
        "",
    )


def _redact_debug_text(value: str) -> str:
    for name in ("OPENAI_USERNAME", "OPENAI_PASSWORD", "OPENAI_2FA_KEY"):
        secret = os.environ.get(name)
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[redacted email]", value, flags=re.I)
    value = re.sub(r"\b\d{6}\b", "[redacted code]", value)
    value = re.sub(r"\b(?:rt|sk)[.-][A-Z0-9._-]{8,}\b", "[redacted token]", value, flags=re.I)
    value = re.sub(r"\beyJ[A-Z0-9_-]{10,}\.[A-Z0-9_-]{10,}(?:\.[A-Z0-9_-]+)?\b", "[redacted token]", value)
    value = re.sub(r"(https?://[^\s?#]+)\?[^\s#]+", r"\1?[redacted]", value)
    return value[:500]


_SCREENSHOT_REDACTION = r"""() => {
  for (const element of document.querySelectorAll('input, textarea, [contenteditable="true"]')) {
    if ('value' in element) element.value = '';
    element.textContent = '';
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    walker.currentNode.textContent = walker.currentNode.textContent
      .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, '[redacted email]')
      .replace(/\b\d{6}\b/g, '[redacted code]')
      .replace(/\b(?:rt|sk)[.-][A-Z0-9._-]{8,}\b/gi, '[redacted token]')
      .replace(/\beyJ[A-Z0-9_-]{10,}\.[A-Z0-9_-]{10,}(?:\.[A-Z0-9_-]+)?\b/g, '[redacted token]');
  }
}"""


def _watch_page_errors(page) -> list[str]:
    errors = []

    def add_error(message):
        if len(errors) < 20:
            errors.append(_redact_debug_text(str(message)))

    try:
        page.on("pageerror", add_error)
        page.on("console", lambda message: add_error(message.text) if message.type == "error" else None)
    except Exception:
        pass
    return errors


def _capture_debug(page, stage: str, step: str, reason: str, errors=()) -> None:
    directory = os.environ.get("CHATGPT_DEBUG_DIR")
    if not directory:
        return
    debug_dir = Path(directory)
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        url = urllib.parse.urlsplit(getattr(page, "url", "")) if page else None
        metadata = {
            "stage": stage,
            "step": step,
            "url": _redact_debug_text(f"{url.scheme}://{url.netloc}{url.path}") if url else "",
            "title": _redact_debug_text(page.title()) if page else "",
            "reason": _redact_debug_text(reason),
            "page_errors": list(errors)[-20:],
        }
        label = re.sub(r"[^a-zA-Z0-9_-]", "_", f"{stage}-{step}")[:80]
        screenshot = debug_dir / f"{label}.png"
        if screenshot.exists():
            metadata["screenshot"] = screenshot.name
        elif page:
            try:
                page.evaluate(_SCREENSHOT_REDACTION)
                page.screenshot(path=str(screenshot), full_page=True)
                screenshot.chmod(0o600)
                metadata["screenshot"] = screenshot.name
            except Exception as error:
                metadata["screenshot_error"] = type(error).__name__
        report = debug_dir / f"{label}.json"
        report.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        report.chmod(0o600)
    except Exception:
        pass


def _find_auth_page(browser):
    for context in browser.contexts:
        for page in context.pages:
            try:
                if ISSUER_HOST in page.url or CALLBACK_PATH in page.url:
                    return page
            except Exception:
                continue
    for context in browser.contexts:
        if context.pages:
            return context.pages[-1]
    return None


def _generate_pkce() -> tuple[str, str]:
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(chars) for _ in range(43))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


STATE_FILE = Path.home() / ".local" / "share" / "opencode" / "chatgpt-login-state.json"


def _state_path() -> Path:
    return STATE_FILE


def _read_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
    STATE_FILE.chmod(0o600)


def _request_stop(signum, frame) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def cmd_stop() -> int:
    state = _read_state()
    pid = state.get("pid") if state else None
    if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
        STATE_FILE.unlink(missing_ok=True)
        print("No active ChatGPT login session")
        return 0
    try:
        os.kill(pid, 0)
        expected = state.get("started_at", 0)
        actual = int(Path(f"/proc/{pid}/stat").read_text().split()[21])
        if expected != actual:
            STATE_FILE.unlink(missing_ok=True)
            print("Removed stale ChatGPT login state")
            return 0
        os.kill(pid, signal.SIGTERM)
        print("Requested graceful stop of ChatGPT login session")
    except (OSError, ValueError, IndexError):
        STATE_FILE.unlink(missing_ok=True)
        print("Removed stale ChatGPT login state")
    return 0


def _authorize_url(verifier: str, state: str) -> str:
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": "opencode",
    }
    return f"{ISSUER}{AUTHORIZE_PATH}?{urllib.parse.urlencode(params)}"


class _CallbackServer:
    """Localhost server that captures the OAuth authorization code."""

    def __init__(self, state: str):
        self.state = state
        self.code: str | None = None
        self.error: str | None = None
        self._server = http.server.HTTPServer(("localhost", CALLBACK_PORT), self._handler_factory())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler_factory(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                if url.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(url.query)
                if qs.get("state", [None])[0] != server.state:
                    server.error = "state mismatch"
                elif qs.get("error_description") or qs.get("error"):
                    server.error = (qs.get("error_description") or qs.get("error"))[0]
                else:
                    server.code = qs.get("code", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h1>Success! You can close this window.</h1></body></html>")

            def log_message(self, *args):
                pass

        return Handler

    def start(self):
        self._thread.start()

    def wait(self, timeout_s=120) -> str | None:
        """Wait for the callback. Returns the code, or None on timeout/error."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.error:
                return None
            if self.code:
                return self.code
            time.sleep(0.5)
        return None

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def _exchange_code(code: str, verifier: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(
        f"{ISSUER}{TOKEN_PATH}",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _do_email_step(page, email: str) -> bool:
    if not _fill_first_visible(page, ['input[type="email"]', 'input[name="email"]', 'input[id="email"]'], email, delay_ms=100):
        return False
    time.sleep(1)
    return _click_first_visible(page, ['button[type="submit"]', 'button:has-text("Continue")'])


def _do_password_step(page, password: str) -> bool:
    if not _fill_first_visible(page, ['input[type="password"]'], password, delay_ms=100):
        return False
    time.sleep(2)
    clicked = _click_first_visible(page, ['button[type="submit"]', 'button:has-text("Continue")'])
    if clicked:
        # Password submit triggers a SPA navigation; give it time to land
        # before callers inspect the page again.
        time.sleep(2)
    return clicked


def _do_2fa_step(page, totp_key: str) -> bool:
    try:
        import pyotp

        code = pyotp.TOTP(totp_key).now()
    except ImportError:
        print("pyotp not installed; run pip install pyotp", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Failed to generate TOTP: {e}", file=sys.stderr)
        return False
    if not _fill_first_visible(page, OTP_SELECTORS, code):
        return False
    return _click_first_visible(page, ['button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Verify")'])


def _do_consent_step(page) -> bool:
    return _click_first_visible(page, ['button[type="submit"]', 'button:has-text("Continue")', 'button:has-text("Allow")'])


def _do_choose_account_step(page) -> bool:
    return _click_first_visible(page, ['button[data-testid*="account"]', 'button:has-text("Select account")'])


def _do_current_step(page, email: str, password: str, totp_key: str | None) -> bool:
    path = _page_path(page)
    if path.startswith("/log-in") and "password" not in path:
        return _do_email_step(page, email)
    if path == "/log-in/password":
        return _do_password_step(page, password)
    if path.startswith("/mfa-challenge"):
        if not totp_key:
            print("2FA required but no TOTP key (OPENAI_2FA_KEY) set", file=sys.stderr)
            return False
        return _do_2fa_step(page, totp_key)
    if path == "/choose-an-account":
        return _do_choose_account_step(page)
    if path.startswith("/sign-in-with-chatgpt"):
        return _do_consent_step(page)
    return False


def cmd_start() -> int:
    global _STOP_REQUESTED
    _STOP_REQUESTED = False
    cdp_port = int(os.environ.get("OPENAI_CDP_PORT", "9222"))

    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("localhost", cdp_port))
        probe.close()
    except OSError:
        print(f"CDP port {cdp_port} already in use", file=sys.stderr)
        return 1

    verifier, _ = _generate_pkce()
    state = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()

    callback = _CallbackServer(state)
    try:
        callback.start()
    except OSError as e:
        print(f"Failed to start callback server on port {CALLBACK_PORT}: {e}", file=sys.stderr)
        return 1

    try:
        started_at = int(Path(f"/proc/{os.getpid()}/stat").read_text().split()[21])
    except (OSError, IndexError, ValueError):
        started_at = None
    _write_state({"verifier": verifier, "state": state, "cdp_port": cdp_port, "code": None,
                  "pid": os.getpid(), "started_at": started_at})
    signal.signal(signal.SIGTERM, _request_stop)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; run pip install playwright", file=sys.stderr)
        callback.close()
        return 1

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, _is_headless(), cdp_port=cdp_port)
        except Exception as e:
            print(f"Failed to launch browser: {e}. If chrome is not installed, run: playwright install chrome", file=sys.stderr)
            callback.close()
            return 1
        context = browser.new_context(user_agent=_DESKTOP_UA, viewport=_DESKTOP_VIEWPORT)
        page = context.new_page()
        page_errors = _watch_page_errors(page)
        try:
            page.goto(_authorize_url(verifier, state), wait_until="domcontentloaded")
        except Exception as e:
            _capture_debug(page, "start", "authorize", str(e), page_errors)
            print(f"Failed to reach auth.openai.com: {e}", file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        _log_step(page, "authorize")
        blocking = _wait_for_blocking_clear(page, timeout_s=120)
        if blocking:
            _capture_debug(page, "start", "authorize", blocking, page_errors)
            print(f"Login blocked: {blocking}", file=sys.stderr)
            browser.close()
            callback.close()
            return 2
        print(
            f"[chatgpt-login] attach: playwright-cli attach --cdp=http://localhost:{cdp_port}; "
            f"drive: scripts/chatgpt-login.py status|step",
            file=sys.stderr,
        )

        while True:
            if _STOP_REQUESTED:
                _capture_debug(page, "start", _step_for_path(_page_path(page)), "stopped", page_errors)
                browser.close()
                callback.close()
                STATE_FILE.unlink(missing_ok=True)
                return 1
            if callback.error:
                _capture_debug(page, "start", _step_for_path(_page_path(page)), callback.error, page_errors)
                print(f"OAuth callback error: {callback.error}", file=sys.stderr)
                browser.close()
                callback.close()
                return 1
            if callback.code:
                break
            time.sleep(1)

        _write_state({"verifier": verifier, "state": state, "cdp_port": cdp_port, "code": callback.code})

        try:
            tokens = _exchange_code(callback.code, verifier)
        except Exception as e:
            _capture_debug(page, "start", "callback", str(e), page_errors)
            print(f"Token exchange failed: {e}", file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        access = tokens.get("access_token", "")
        refresh = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)
        if not access or not refresh:
            _capture_debug(page, "start", "callback", "token exchange returned no tokens", page_errors)
            print("Token exchange returned no access/refresh token", file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        try:
            auth = build_auth_json(access, refresh, int(time.time() * 1000) + int(expires_in) * 1000)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        write_auth_file(auth)
        json.dump(auth, sys.stdout)
        sys.stdout.write("\n")
        browser.close()
        callback.close()
        STATE_FILE.unlink(missing_ok=True)
        return 0


def cmd_status() -> int:
    state = _read_state()
    if state is None:
        json.dump({"error": "no login session"}, sys.stdout)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        json.dump({"error": "playwright not installed"}, sys.stdout)
        return 1

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{state['cdp_port']}")
        except Exception:
            json.dump({"error": "browser not running"}, sys.stdout)
            return 1
        page = _find_auth_page(browser)
        if page is None:
            json.dump({"error": "no pages"}, sys.stdout)
            return 1
        try:
            step = _step_for_path(_page_path(page))
            blocking = _has_blocking_screen(page)
            if blocking:
                _capture_debug(page, "primitives", step or "unknown", blocking)
            code_ready = state.get("code") is not None
            json.dump({"step": step, "url": page.url.split("?", 1)[0].split("#", 1)[0], "title": page.title(), "blocking": blocking, "code_ready": code_ready}, sys.stdout)
        except Exception as e:
            json.dump({"error": str(e)}, sys.stdout)
            return 1
        return 0


def cmd_step() -> int:
    state = _read_state()
    if state is None:
        json.dump({"error": "no login session"}, sys.stdout)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        json.dump({"error": "playwright not installed"}, sys.stdout)
        return 1

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{state['cdp_port']}")
        except Exception:
            json.dump({"error": "browser not running"}, sys.stdout)
            return 1
        page = _find_auth_page(browser)
        if page is None:
            json.dump({"error": "no pages"}, sys.stdout)
            return 1

        if state.get("code") is not None:
            json.dump({"step": "done"}, sys.stdout)
            return 0

        email = os.environ.get("OPENAI_USERNAME")
        password = os.environ.get("OPENAI_PASSWORD")
        totp_key = os.environ.get("OPENAI_2FA_KEY")
        if not email or not password:
            print("OPENAI_USERNAME and OPENAI_PASSWORD must be set", file=sys.stderr)
            return 1

        page_errors = _watch_page_errors(page)
        blocking = _wait_for_blocking_clear(page, timeout_s=15)
        if blocking:
            _capture_debug(page, "primitives", _step_for_path(_page_path(page)), blocking, page_errors)
            print(f"Login blocked: {blocking}", file=sys.stderr)
            try:
                step = _step_for_path(_page_path(page))
                json.dump({"step": step, "url": page.url.split("?", 1)[0].split("#", 1)[0], "title": page.title(), "blocking": blocking, "code_ready": state.get("code") is not None}, sys.stdout)
            except Exception:
                json.dump({"error": "status failed while blocked", "blocking": blocking}, sys.stdout)
            return 2

        ok = _do_current_step(page, email, password, totp_key)
        if not ok:
            step_name = _step_for_path(_page_path(page)) or "unknown"
            _capture_debug(page, "primitives", step_name, "automation step made no progress", page_errors)
            print(
                f"[chatgpt-login] no progress at step {step_name} — attach with playwright-cli attach --cdp=http://localhost:{state.get('cdp_port', 9222)}",
                file=sys.stderr,
            )
        try:
            step = _step_for_path(_page_path(page))
            blocking = _has_blocking_screen(page)
            code_ready = state.get("code") is not None
            json.dump({"step": step, "url": page.url.split("?", 1)[0].split("#", 1)[0], "title": page.title(), "blocking": blocking, "code_ready": code_ready}, sys.stdout)
        except Exception as e:
            json.dump({"error": str(e)}, sys.stdout)
            return 1
        return 0 if ok else 1


def _full_login() -> int:
    email = os.environ.get("OPENAI_USERNAME")
    password = os.environ.get("OPENAI_PASSWORD")
    totp_key = os.environ.get("OPENAI_2FA_KEY")

    if not email or not password:
        print("OPENAI_USERNAME and OPENAI_PASSWORD must be set", file=sys.stderr)
        return 1

    verifier, _ = _generate_pkce()
    state = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()

    callback = _CallbackServer(state)
    try:
        callback.start()
    except OSError as e:
        print(f"Failed to start callback server on port {CALLBACK_PORT}: {e}", file=sys.stderr)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright not installed; run pip install playwright", file=sys.stderr)
        callback.close()
        return 1

    with sync_playwright() as p:
        try:
            browser = _launch_browser(p, _is_headless())
        except Exception as e:
            print(f"Failed to launch browser: {e}. If chrome is not installed, run: playwright install chrome", file=sys.stderr)
            callback.close()
            return 1
        context = browser.new_context(user_agent=_DESKTOP_UA, viewport=_DESKTOP_VIEWPORT)
        page = context.new_page()
        page_errors = _watch_page_errors(page)
        try:
            page.goto(_authorize_url(verifier, state), wait_until="domcontentloaded")
        except Exception as e:
            _capture_debug(page, "browser", "authorize", str(e), page_errors)
            print(f"Failed to reach auth.openai.com: {e}", file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        _log_step(page, "authorize")

        blocking = _wait_for_blocking_clear(page, timeout_s=120)
        if blocking:
            _capture_debug(page, "browser", "authorize", blocking, page_errors)
            print(f"Login blocked: {blocking} — cannot automate with credentials alone", file=sys.stderr)
            browser.close()
            callback.close()
            return 2

        # Drive the login state machine until the callback fires. Step
        # functions may "fail" even when the click worked (playwright raises
        # when the element detaches during navigation), so the loop just
        # re-checks the page state each iteration and only gives up when a
        # step makes no progress.
        deadline = time.monotonic() + 180
        last_step = ""
        attempts: dict[str, int] = {}
        while time.monotonic() < deadline:
            if callback.code or callback.error:
                break
            path = _page_path(page)
            step = _step_for_path(path)
            if step and step != last_step:
                _log_step(page, step)
                last_step = step
                attempts[step] = 0
            # A bot challenge can appear mid-flow (Cloudflare interstitial after
            # the password submit). Wait for it to clear before counting another
            # attempt, otherwise blocked time burns the attempt budget and the
            # flow exits with a misleading "Stuck at step".
            blocking = _wait_for_blocking_clear(page, timeout_s=90)
            if blocking:
                _capture_debug(page, "browser", step or path, blocking, page_errors)
                print(f"Login blocked: {blocking} — cannot automate with credentials alone", file=sys.stderr)
                _dump_page_state(page)
                browser.close()
                callback.close()
                return 2
            if step:
                attempts[step] = attempts.get(step, 0) + 1
                if attempts[step] > 5:
                    _capture_debug(page, "browser", step, "step retry limit exceeded", page_errors)
                    print(f"Stuck at step {step}", file=sys.stderr)
                    _dump_page_state(page)
                    browser.close()
                    callback.close()
                    return 1

            try:
                if path.startswith("/log-in") and "password" not in path:
                    _do_email_step(page, email)
                elif path == "/log-in/password":
                    _do_password_step(page, password)
                    # The SPA may show an error page; retry a couple of times
                    # before a persistent one counts as blocking (below).
                    for attempt in range(2):
                        if not _is_error_page(page):
                            break
                        print(f"[chatgpt-login] OpenAI error page after password submit, retry {attempt + 1}/2", file=sys.stderr)
                        time.sleep(2)
                        _click_first_visible(page, ['a:has-text("Try again")', 'button:has-text("Try again")'])
                        time.sleep(1)
                        _do_password_step(page, password)
                    blocking = _wait_for_blocking_clear(page, timeout_s=90)
                    if blocking:
                        _capture_debug(page, "browser", step, blocking, page_errors)
                        print(f"Login blocked after password: {blocking}", file=sys.stderr)
                        browser.close()
                        callback.close()
                        return 2
                elif path.startswith("/mfa-challenge"):
                    if not totp_key:
                        print("2FA required but no TOTP key (OPENAI_2FA_KEY) set", file=sys.stderr)
                        browser.close()
                        callback.close()
                        return 1
                    _do_2fa_step(page, totp_key)
                elif path == "/choose-an-account":
                    _do_choose_account_step(page)
                elif path.startswith("/sign-in-with-chatgpt"):
                    _do_consent_step(page)
                elif path == CALLBACK_PATH:
                    break
            except Exception as e:
                _capture_debug(page, "browser", step or path, str(e), page_errors)
                print(f"Failed at step {step or path}: {e}", file=sys.stderr)
                _dump_page_state(page)
                browser.close()
                callback.close()
                return 1

            time.sleep(1)

        code = callback.wait(timeout_s=10)
        if callback.error:
            print(f"OAuth callback error: {callback.error}", file=sys.stderr)
            browser.close()
            callback.close()
            return 1
        if not code:
            _capture_debug(page, "browser", _step_for_path(_page_path(page)), "timed out waiting for OAuth callback", page_errors)
            print("Timed out waiting for OAuth callback", file=sys.stderr)
            _dump_page_state(page)
            browser.close()
            callback.close()
            return 1

        _log_step(page, "callback")

        try:
            tokens = _exchange_code(code, verifier)
        except Exception as e:
            _capture_debug(page, "browser", "callback", str(e), page_errors)
            print(f"Token exchange failed: {e}", file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        access = tokens.get("access_token", "")
        refresh = tokens.get("refresh_token", "")
        expires_in = tokens.get("expires_in", 3600)
        if not access or not refresh:
            _capture_debug(page, "browser", "callback", "token exchange returned no tokens", page_errors)
            print("Token exchange returned no access/refresh token", file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        try:
            auth = build_auth_json(access, refresh, int(time.time() * 1000) + int(expires_in) * 1000)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            browser.close()
            callback.close()
            return 1

        write_auth_file(auth)
        json.dump(auth, sys.stdout)
        sys.stdout.write("\n")
        browser.close()
        callback.close()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["start", "status", "step", "stop"])
    args = parser.parse_args()
    if args.command == "start":
        return cmd_start()
    if args.command == "status":
        return cmd_status()
    if args.command == "step":
        return cmd_step()
    if args.command == "stop":
        return cmd_stop()
    return _full_login()


if __name__ == "__main__":
    raise SystemExit(main())
