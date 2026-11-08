import importlib.util
import inspect
import json
import os
import subprocess
import types
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "chatgpt_login", Path(__file__).with_name("chatgpt-login.py")
)
assert spec and spec.loader
chatgpt_login = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chatgpt_login)


def test_build_auth_json_shape():
    auth = chatgpt_login.build_auth_json("acc", "ref", 1234567890000)
    assert auth == {
        "openai": {"type": "oauth", "access": "acc", "refresh": "ref", "expires": 1234567890000}
    }


def test_step_for_nested_consent_route():
    assert chatgpt_login._step_for_path("/sign-in-with-chatgpt/codex/consent") == "consent"


def test_debug_text_redacts_credentials_and_authorization_query(monkeypatch):
    monkeypatch.setenv("OPENAI_PASSWORD", "private-password")
    result = chatgpt_login._redact_debug_text(
        "private-password user@example.com https://auth.openai.com/?code=private-code"
    )
    assert "private-password" not in result
    assert "user@example.com" not in result
    assert "private-code" not in result
    assert "[redacted]" in result


def test_headless_selection_defaults_to_no_display(monkeypatch):
    monkeypatch.delenv("CHATGPT_HEADLESS", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert chatgpt_login._is_headless()

    monkeypatch.setenv("DISPLAY", ":99")
    assert not chatgpt_login._is_headless()
    monkeypatch.setenv("CHATGPT_HEADLESS", "true")
    assert chatgpt_login._is_headless()


def test_browser_startup_diagnostic_is_actionable_and_redacted(monkeypatch):
    monkeypatch.setenv("OPENAI_PASSWORD", "secret-password")
    diagnostic = chatgpt_login._browser_startup_error(
        RuntimeError("secret-password failed to connect"), True
    )
    assert "Browser startup failed" in diagnostic
    assert "headless=True" in diagnostic
    assert "secret-password" not in diagnostic
    assert "playwright install --with-deps chrome" in diagnostic


def test_launch_browser_uses_chrome_channel_only():
    source = Path(chatgpt_login.__file__).read_text(encoding="utf-8")
    launch = source[source.index("def _launch_browser") : source.index("def _credential")]
    assert 'channel="chrome"' in launch
    assert "except Exception" in launch


def test_credential_aliases(monkeypatch):
    monkeypatch.delenv("OPENAI_USERNAME", raising=False)
    monkeypatch.setenv("CHATGPT_EMAIL", "chatgpt@example.com")
    monkeypatch.delenv("OPENAI_PASSWORD", raising=False)
    monkeypatch.setenv("CHATGPT_PASSWORD", "password")
    monkeypatch.delenv("OPENAI_2FA_KEY", raising=False)
    monkeypatch.setenv("CHATGPT_TOTP_KEY", "totp")
    assert chatgpt_login._credential("OPENAI_USERNAME", "CHATGPT_EMAIL") == "chatgpt@example.com"
    assert chatgpt_login._credential("OPENAI_PASSWORD", "CHATGPT_PASSWORD") == "password"
    assert chatgpt_login._credential("OPENAI_2FA_KEY", "CHATGPT_2FA_KEY", "CHATGPT_TOTP_KEY") == "totp"


def test_chatgpt_credentials_take_precedence(monkeypatch):
    monkeypatch.setenv("OPENAI_USERNAME", "openai@example.com")
    monkeypatch.setenv("CHATGPT_EMAIL", "chatgpt@example.com")
    monkeypatch.setenv("OPENAI_PASSWORD", "openai-password")
    monkeypatch.setenv("CHATGPT_PASSWORD", "chatgpt-password")
    monkeypatch.setenv("OPENAI_2FA_KEY", "openai-key")
    monkeypatch.setenv("CHATGPT_2FA_KEY", "chatgpt-key")
    monkeypatch.setenv("CHATGPT_TOTP_KEY", "chatgpt-totp")
    assert chatgpt_login._credential("CHATGPT_EMAIL", "OPENAI_USERNAME") == "chatgpt@example.com"
    assert chatgpt_login._credential("CHATGPT_PASSWORD", "OPENAI_PASSWORD") == "chatgpt-password"
    assert chatgpt_login._credential("CHATGPT_TOTP_KEY", "CHATGPT_2FA_KEY", "OPENAI_2FA_KEY") == "chatgpt-totp"


def test_stop_removes_stale_login_state(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    monkeypatch.setattr(chatgpt_login, "STATE_FILE", state_file)
    assert chatgpt_login.cmd_stop() == 0
    assert not state_file.exists()
    assert "No active ChatGPT login session" in capsys.readouterr().out


def test_debug_screenshot_skips_unusually_long_page_text(tmp_path, monkeypatch):
    class Page:
        url = "https://auth.openai.com/sign-in"

        def title(self):
            return "Sign in"

        def inner_text(self, selector):
            return "x" * 1201

        def evaluate(self, script):
            raise AssertionError("screenshot should be skipped")

    monkeypatch.setenv("CHATGPT_DEBUG_DIR", str(tmp_path))
    chatgpt_login._capture_debug(Page(), "browser", "authorize", "blocked")
    report = json.loads((tmp_path / "browser-authorize.json").read_text())
    assert report["screenshot_skipped"] == "page text exceeds safe capture limit"
    assert not (tmp_path / "browser-authorize.png").exists()


def test_build_auth_json_rejects_empty_access():
    with pytest.raises(ValueError):
        chatgpt_login.build_auth_json("", "ref", 1)


def test_build_auth_json_rejects_empty_refresh():
    with pytest.raises(ValueError):
        chatgpt_login.build_auth_json("acc", "", 1)


def test_build_auth_json_rejects_non_int_expires():
    with pytest.raises(ValueError):
        chatgpt_login.build_auth_json("acc", "ref", "123")
    with pytest.raises(ValueError):
        chatgpt_login.build_auth_json("acc", "ref", 12.3)


def test_write_auth_file_explicit_path(tmp_path):
    dest = tmp_path / "nested" / "dir" / "auth.json"
    auth = chatgpt_login.build_auth_json("a", "r", 999)
    assert chatgpt_login.write_auth_file(auth, dest) == dest
    assert dest.parent.is_dir()
    assert json.loads(dest.read_text(encoding="utf-8")) == auth


def test_write_auth_file_default_path(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    auth = chatgpt_login.build_auth_json("a", "r", 999)
    dest = chatgpt_login.write_auth_file(auth)
    assert dest == tmp_path / ".local" / "share" / "opencode" / "auth.json"
    assert json.loads(dest.read_text(encoding="utf-8")) == auth


_CLOUDFLARE_BODY = """
<html><head><title>Just a moment...</title></head>
<body>
  <h1>Performing security verification</h1>
  <p>This website uses a security service to protect itself from malicious bots.</p>
  <p>Ray ID: a4acbfc91ba5646a</p>
</body></html>
"""


class _FakeLocator:
    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return False


class _FakePage:
    def __init__(self, content, title="", url="https://auth.openai.com/log-in"):
        self._content = content
        self._title = title
        self.url = url

    def content(self):
        return self._content

    def inner_text(self, selector):
        return self._content

    def title(self):
        return self._title

    def locator(self, selector):
        return _FakeLocator()


def test_has_blocking_screen_detects_cloudflare_interstitial():
    assert chatgpt_login._has_blocking_screen(_FakePage(_CLOUDFLARE_BODY)) == (
        "cloudflare challenge detected: just a moment"
    )


def test_has_blocking_screen_returns_none_for_normal_page():
    page = _FakePage("<html><body><h1>Sign in</h1></body></html>")
    assert chatgpt_login._has_blocking_screen(page) is None


class _FakeTime:
    def __init__(self, start=0.0):
        self._now = start
        self.sleeps = []

    def monotonic(self):
        return self._now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self._now += seconds


def test_email_step_types_slowly_and_pauses_before_submit(monkeypatch):
    fake_time = _FakeTime()
    monkeypatch.setattr(chatgpt_login, "time", fake_time)
    page = _FillPage()
    assert chatgpt_login._do_email_step(page, "user@example.com")
    assert page.locator_instance.typed == [("user@example.com", 200)]
    assert fake_time.sleeps == [2.0]


def test_password_step_pauses_and_waits_for_post_submit_blocking(monkeypatch):
    fake_time = _FakeTime()
    waits = []
    monkeypatch.setattr(chatgpt_login, "time", fake_time)
    monkeypatch.setattr(
        chatgpt_login,
        "_wait_for_blocking_clear",
        lambda page, timeout_s: waits.append(timeout_s),
    )
    page = _FillPage()
    assert chatgpt_login._do_password_step(page, "hunter2")
    assert page.locator_instance.typed == [("hunter2", 200)]
    assert fake_time.sleeps == [4.0, 8.0]
    assert waits == [30]


def test_password_step_skips_post_submit_pause_when_click_fails(monkeypatch):
    fake_time = _FakeTime()
    monkeypatch.setattr(chatgpt_login, "time", fake_time)
    monkeypatch.setattr(chatgpt_login, "_click_first_visible", lambda *args, **kwargs: False)
    page = _FillPage()
    assert not chatgpt_login._do_password_step(page, "hunter2")
    assert fake_time.sleeps == [4.0]


def test_wait_for_blocking_clear_defaults_to_90s():
    signature = inspect.signature(chatgpt_login._wait_for_blocking_clear)
    assert signature.parameters["timeout_s"].default == 90


def test_wait_for_blocking_clear_polls_blocked_page_with_5s_sleeps(monkeypatch):
    fake_time = _FakeTime()
    monkeypatch.setattr(chatgpt_login, "time", fake_time)
    blocked = chatgpt_login._wait_for_blocking_clear(_FakePage(_CLOUDFLARE_BODY))
    assert blocked == "cloudflare challenge detected: just a moment"
    assert fake_time.sleeps == [5.0] * 18


def test_wait_for_blocking_clear_returns_none_immediately_for_clean_page(monkeypatch):
    fake_time = _FakeTime()
    monkeypatch.setattr(chatgpt_login, "time", fake_time)
    page = _FakePage("<html><body><h1>Sign in</h1></body></html>")
    assert chatgpt_login._wait_for_blocking_clear(page) is None
    assert fake_time.sleeps == []


def test_start_keeps_browser_session_alive_when_authorize_page_is_blocked(monkeypatch, tmp_path, capsys):
    class _Socket:
        def bind(self, address):
            pass

        def close(self):
            pass

    class _Page:
        url = "https://auth.openai.com/oauth/authorize"

        def on(self, *args):
            pass

        def goto(self, *args, **kwargs):
            pass

        def title(self):
            return "Just a moment..."

    class _Browser:
        closed = False

        def new_context(self, **kwargs):
            return types.SimpleNamespace(new_page=lambda: page)

        def close(self):
            self.closed = True

    class _Callback:
        closed = False

        def __init__(self, state):
            self.error = None
            self.code = None

        def start(self):
            pass

        def close(self):
            self.closed = True

    page = _Page()
    browser = _Browser()
    callback = _Callback("state")
    fake_time = _FakeTime()
    monkeypatch.setattr(chatgpt_login.socket, "socket", lambda *args: _Socket())
    monkeypatch.setattr(chatgpt_login, "_CallbackServer", lambda state: callback)
    monkeypatch.setattr(chatgpt_login, "_write_state", lambda data: None)
    monkeypatch.setattr(chatgpt_login, "_watch_page_errors", lambda page: [])
    monkeypatch.setattr(chatgpt_login, "_capture_debug", lambda *args: None)
    monkeypatch.setattr(chatgpt_login, "_has_blocking_screen", lambda page: "cloudflare challenge")
    monkeypatch.setattr(
        chatgpt_login,
        "_wait_for_blocking_clear",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("startup must not wait out the challenge")),
    )
    monkeypatch.setattr(chatgpt_login, "_launch_browser", lambda *args, **kwargs: browser)
    monkeypatch.setattr(chatgpt_login.signal, "signal", lambda *args: None)

    def sleep(seconds):
        assert not browser.closed
        chatgpt_login._STOP_REQUESTED = True

    fake_time.sleep = sleep
    monkeypatch.setattr(chatgpt_login, "time", fake_time)
    monkeypatch.setattr(chatgpt_login, "STATE_FILE", tmp_path / "state.json")

    import playwright.sync_api

    class _SyncPlaywright:
        chromium = types.SimpleNamespace(launch=lambda **kwargs: browser)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: _SyncPlaywright())
    monkeypatch.setattr(chatgpt_login, "_STOP_REQUESTED", False)

    assert chatgpt_login.cmd_start() == 1
    assert browser.closed
    assert callback.closed
    assert "Login blocked: cloudflare challenge" in capsys.readouterr().err


class _RecordingLocator:
    def __init__(self):
        self.fills = []
        self.typed = []
        self.clicks = []

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return True

    def fill(self, value):
        self.fills.append(value)

    def press_sequentially(self, value, delay=None):
        self.typed.append((value, delay))

    def click(self):
        self.clicks.append(True)


class _FillPage:
    def __init__(self):
        self.locator_instance = _RecordingLocator()

    def locator(self, selector):
        return self.locator_instance


def test_fill_first_visible_with_delay_clears_then_types_full_value():
    page = _FillPage()
    assert chatgpt_login._fill_first_visible(
        page, ['input[type="email"]'], "user@example.com", delay_ms=50
    )
    assert page.locator_instance.fills == [""]
    assert page.locator_instance.typed == [("user@example.com", 50)]


def test_fill_first_visible_without_delay_uses_plain_fill():
    page = _FillPage()
    assert chatgpt_login._fill_first_visible(page, ['input[type="email"]'], "user@example.com")
    assert page.locator_instance.fills == ["user@example.com"]
    assert page.locator_instance.typed == []


def test_refresh_workflow_suppresses_failed_auth_output_and_masks_stored_auth():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    browser = workflow[
        workflow.index("- name: Browser login (fallback)") : workflow.index(
            "- name: Agent-usable primitives fallback"
        )
    ]
    assert 'cat "$RUNNER_TEMP/auth.json" >&2' not in browser
    assert 'cat "$RUNNER_TEMP/chatgpt-login.stderr" >&2' not in browser
    assert "Browser login failed; authentication output suppressed." in browser
    assert 'exit "$status"' in browser

    for step in ("Set secret (refreshed token)", "Browser login (fallback)"):
        start = workflow.index(f"- name: {step}")
        end = workflow.find("\n      - name:", start + 1)
        block = workflow[start : end if end >= 0 else len(workflow)]
        assert 'printf \'::add-mask::%s\\n\' "$auth_json"' in block
        assert 'gh secret set OPENCODE_AUTH_JSON' in block


def test_primitives_fallback_observes_retries_and_validates_login_output():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    primitives = workflow[
        workflow.index("- name: Agent-usable primitives fallback") :
    ]
    helper = (Path(__file__).with_name("chatgpt-login-primitives-fallback.sh")).read_text(encoding="utf-8")
    assert "if: ${{ !cancelled() && (inputs.force_primitives || steps.browser-login.outcome == 'failure') }}" in primitives
    assert "scripts/chatgpt-login-primitives-fallback.sh" in primitives
    assert "scripts/chatgpt-login.py start >" in helper
    assert 'CHATGPT_HEADLESS: "true"' in primitives
    assert "credentials are not configured" in helper
    assert "Primitive login startup failed; safe diagnostics:" in helper
    assert 'cat "$stderr_file" >&2' in helper
    assert "Primitive login step failed with status" in helper
    assert "for attempt in $(seq 1 120)" in helper
    assert "if ! status_json=$(uv run scripts/chatgpt-login.py status); then" in helper
    assert "scripts/chatgpt-login.py step) || step_status=$?" in helper
    assert "Primitive login timed out" in helper
    assert "Primitive login reported invalid status" in helper
    assert "jq -r '.blocking // empty'" in helper
    assert 'jq -e \'.openai.type == "oauth"' in helper
    assert 'gh secret set OPENCODE_AUTH_JSON' in helper
    assert "scripts/chatgpt-login.py status || true" not in helper
    assert "scripts/chatgpt-login.py step || true" not in helper


def test_primitives_fallback_steps_after_a_blocking_status():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    helper = (Path(__file__).with_name("chatgpt-login-primitives-fallback.sh")).read_text(encoding="utf-8")
    blocking_start = helper.index("if [[ -n \"$(jq -r '.blocking // empty' <<< \"$status_json\")\" ]]; then")
    blocking_end = helper.index("continue", blocking_start)
    assert "sleep 5" in helper[blocking_start:blocking_end]


_OPENAI_ERROR_BODY = "<html><body><h1>Oops, an error occurred!</h1></body></html>"


def test_refresh_workflow_uses_opencode_v1_auth_and_validates_before_storing():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    refresh = workflow[
        workflow.index("- name: Try token refresh (no browser)") : workflow.index(
            "- name: Validate refreshed token with opencode"
        )
    ]
    validate = workflow[
        workflow.index("- name: Validate refreshed token with opencode") : workflow.index(
            "- name: Set secret (refreshed token)"
        )
    ]
    store = workflow[
        workflow.index("- name: Set secret (refreshed token)") : workflow.index(
            "- name: Browser login (fallback)"
        )
    ]
    assert "OPENCODE_AUTH_CONTENT: ${{ secrets.OPENCODE_AUTH_JSON }}" in refresh
    assert "scripts/refresh-token.py" in refresh
    assert "npm install --global opencode-ai@1.18.32" in validate
    assert "--model openai/gpt-5.6-luna" in validate
    assert "steps.validate.outcome == 'success'" in store
    assert "!cancelled() && !inputs.force_primitives && (steps.refresh.outputs.refreshed != 'true' || steps.validate.outcome != 'success')" in workflow
    assert "CHATGPT_REFRESH_TOKEN" not in workflow
    assert 'CHATGPT_EMAIL: ${{ secrets.CHATGPT_EMAIL || secrets.OPENAI_USERNAME }}' in workflow
    assert 'CHATGPT_HEADLESS: "true"' in workflow
    assert "Require refreshed authentication" in workflow
    assert '.openai.type == "oauth"' in workflow


def test_force_primitives_input_gates_the_refresh_step():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    assert "force_primitives:" in workflow
    refresh = workflow[
        workflow.index("- name: Try token refresh (no browser)") : workflow.index(
            "- name: Browser login (fallback)"
        )
    ]
    assert "if: ${{ !inputs.force_primitives }}" in refresh


def test_has_blocking_screen_detects_openai_error_page():
    assert chatgpt_login._has_blocking_screen(_FakePage(_OPENAI_ERROR_BODY)) == (
        "openai error page detected"
    )


def _stub_browser(monkeypatch, page):
    import playwright.sync_api

    class _FakeChromium:
        def connect_over_cdp(self, *args, **kwargs):
            return types.SimpleNamespace(contexts=[types.SimpleNamespace(pages=[page])])

    class _FakePlaywright:
        chromium = _FakeChromium()

    class _FakeSyncPlaywright:
        def __enter__(self):
            return _FakePlaywright()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        playwright.sync_api, "sync_playwright", lambda: _FakeSyncPlaywright()
    )


def _stub_auth_env(monkeypatch):
    monkeypatch.setattr(
        chatgpt_login, "_read_state", lambda: {"code": None, "cdp_port": 9222}
    )
    monkeypatch.setenv("OPENAI_USERNAME", "user@example.com")
    monkeypatch.setenv("OPENAI_PASSWORD", "hunter2")


def test_cmd_status_reports_the_openai_error_page_as_blocking(monkeypatch, capsys):
    _stub_browser(monkeypatch, _FakePage(_OPENAI_ERROR_BODY))
    _stub_auth_env(monkeypatch)
    assert chatgpt_login.cmd_status() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["blocking"] == "openai error page detected"
    assert payload["step"] == "email"


def test_cmd_step_emits_blocking_status_json_on_the_openai_error_page(monkeypatch, capsys):
    _stub_browser(monkeypatch, _FakePage(_OPENAI_ERROR_BODY))
    _stub_auth_env(monkeypatch)
    monkeypatch.setattr(chatgpt_login, "time", _FakeTime())
    assert chatgpt_login.cmd_step() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["blocking"] == "openai error page detected"


def test_cmd_step_emits_status_json_when_a_step_makes_no_progress(monkeypatch, capsys):
    _stub_browser(monkeypatch, _FakePage("<html><body><h1>Sign in</h1></body></html>"))
    _stub_auth_env(monkeypatch)
    monkeypatch.setattr(chatgpt_login, "_do_current_step", lambda *a, **k: False)
    assert chatgpt_login.cmd_step() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["step"] == "email"
    assert payload["code_ready"] is False


def test_full_login_retries_the_password_error_page_before_treating_it_as_blocking():
    source = Path(chatgpt_login.__file__).read_text(encoding="utf-8")
    full_login = source[source.index("def _full_login") : source.index("def main")]
    assert full_login.index("OpenAI error page after password submit") < full_login.index(
        "Login blocked after password"
    )


def test_primitives_fallback_recovers_or_fails_closed_and_cleans_up(tmp_path):
    helper = Path(__file__).with_name("chatgpt-login-primitives-fallback.sh")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv").write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
case "${3:-}" in
  start)
    trap 'touch "$RUNNER_TEMP/stopped"; exit 143' TERM INT
    if [[ "$FAKE_MODE" == success || "$FAKE_MODE" == recovered ]]; then
      printf '%s\\n' '{"openai":{"type":"oauth","access":"test-access","refresh":"test-refresh","expires":123}}'
    elif [[ "$FAKE_MODE" == invalid-output ]]; then
      printf '%s\\n' '{"openai":{"type":"oauth","access":"","refresh":"blocked-refresh","expires":123}}'
    else
      printf '%s\\n' '{"openai":{"type":"oauth","access":"blocked-access","refresh":"blocked-refresh","expires":123}}'
    fi
    while [[ ! -e "$RUNNER_TEMP/release-start" ]]; do sleep 0.01; done
    if [[ "$FAKE_MODE" == blocked ]]; then exit 1; fi
    ;;
  status)
    if [[ "$FAKE_MODE" == success || "$FAKE_MODE" == invalid-output ]]; then
      touch "$RUNNER_TEMP/release-start"
      printf '%s\\n' '{"step":"done","blocking":null,"code_ready":true}'
    elif [[ "$FAKE_MODE" == recovered && -e "$RUNNER_TEMP/recovered" ]]; then
      touch "$RUNNER_TEMP/release-start"
      printf '%s\\n' '{"step":"done","blocking":null,"code_ready":true}'
    elif [[ "$FAKE_MODE" == invalid-status ]]; then
      touch "$RUNNER_TEMP/release-start"
      printf '%s\\n' 'not-json'
    else
      touch "$RUNNER_TEMP/release-start"
      printf '%s\\n' '{"step":"email","blocking":"challenge","code_ready":false}'
    fi
    ;;
  step)
    printf '%s\\n' '{"step":"email","blocking":null,"code_ready":false}'
    ;;
  stop)
    touch "$RUNNER_TEMP/stopped"
    ;;
esac
""",
        encoding="utf-8",
    )
    (fake_bin / "uv").chmod(0o755)
    (fake_bin / "gh").write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" > "$RUNNER_TEMP/gh-call"
""",
        encoding="utf-8",
    )
    (fake_bin / "gh").chmod(0o755)
    (fake_bin / "playwright-cli").write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\\n' "$*" >> "$RUNNER_TEMP/playwright-cli-calls"
if [[ "$FAKE_MODE" == cli-failure ]]; then
  printf '%s\\n' 'secret-password cli failure' >&2
  exit 1
fi
if [[ "$FAKE_MODE" == snapshot-partial-failure && "${2:-}" == snapshot ]]; then
  printf '%s\\n' 'partial snapshot secret-password'
  exit 1
fi
if [[ "$FAKE_MODE" == snapshot-failure && "${2:-}" == snapshot ]]; then
  printf '%s\\n' 'secret-password snapshot failure' >&2
  exit 1
fi
if [[ "$FAKE_MODE" == recovered && "${2:-}" == snapshot ]]; then
  touch "$RUNNER_TEMP/recovered"
  printf '%s\\n' 'inspectable snapshot'
fi
""",
        encoding="utf-8",
    )
    (fake_bin / "playwright-cli").chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_REPOSITORY": "example/repository",
        "BACLUC_AGENT_GITHUB_TOKEN": "test-token",
        "CHATGPT_EMAIL": "test@example.invalid",
        "CHATGPT_PASSWORD": "test-password",
        "CHATGPT_HEADLESS": "true",
    }

    success = subprocess.run(
        ["bash", str(helper)], env={**env, "FAKE_MODE": "success"}, capture_output=True, text=True, timeout=10
    )
    assert success.returncode == 0
    assert "secret set OPENCODE_AUTH_JSON" in (tmp_path / "gh-call").read_text()
    assert "test-access" in (tmp_path / "auth.json").read_text()

    (tmp_path / "gh-call").unlink()
    recovered = subprocess.run(
        ["bash", str(helper)], env={**env, "FAKE_MODE": "recovered"}, capture_output=True, text=True, timeout=10
    )
    assert recovered.returncode == 0
    assert "test-access" in (tmp_path / "auth.json").read_text()
    snapshot = tmp_path / "chatgpt-login-recovery.snapshot"
    assert snapshot.read_text() == "inspectable snapshot\n"
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "playwright-cli-calls").read_text().splitlines() == [
        "-s=chatgpt-auth-recovery attach --cdp=http://127.0.0.1:9222",
        "-s=chatgpt-auth-recovery snapshot",
        "-s=chatgpt-auth-recovery detach",
    ]
    assert "Playwright handoff retained" in recovered.stderr
    handoff = tmp_path / "chatgpt-login-recovery.handoff"
    assert handoff.read_text() == f"""session=chatgpt-auth-recovery
cdp=http://127.0.0.1:9222
snapshot={snapshot}
attach=playwright-cli -s=chatgpt-auth-recovery attach --cdp=http://127.0.0.1:9222
inspect=playwright-cli -s=chatgpt-auth-recovery snapshot
status=uv run scripts/chatgpt-login.py status
step=uv run scripts/chatgpt-login.py step
detach=playwright-cli -s=chatgpt-auth-recovery detach
"""
    assert handoff.stat().st_mode & 0o777 == 0o600
    assert "reload" not in recovered.stderr.lower()

    (tmp_path / "playwright-cli-calls").unlink()
    snapshot_failure = subprocess.run(
        ["bash", str(helper)], env={**env, "FAKE_MODE": "snapshot-partial-failure"}, capture_output=True, text=True, timeout=10
    )
    assert snapshot_failure.returncode != 0
    assert "secret-password" not in snapshot_failure.stderr
    assert not snapshot.exists()
    assert not handoff.exists()
    assert (tmp_path / "playwright-cli-calls").read_text().splitlines() == [
        "-s=chatgpt-auth-recovery attach --cdp=http://127.0.0.1:9222",
        "-s=chatgpt-auth-recovery snapshot",
        "-s=chatgpt-auth-recovery detach",
    ]

    (tmp_path / "gh-call").unlink()
    (tmp_path / "playwright-cli-calls").unlink()
    blocked = subprocess.run(
        ["bash", str(helper)], env={**env, "FAKE_MODE": "blocked"}, capture_output=True, text=True, timeout=10
    )
    assert blocked.returncode != 0
    assert (tmp_path / "stopped").exists()
    assert not (tmp_path / "gh-call").exists()
    assert (tmp_path / "playwright-cli-calls").read_text().splitlines() == [
        "-s=chatgpt-auth-recovery attach --cdp=http://127.0.0.1:9222",
        "-s=chatgpt-auth-recovery snapshot",
        "-s=chatgpt-auth-recovery detach",
    ]

    (tmp_path / "stopped").unlink()
    (tmp_path / "playwright-cli-calls").unlink()
    cli_failure = subprocess.run(
        ["bash", str(helper)], env={**env, "FAKE_MODE": "cli-failure"}, capture_output=True, text=True, timeout=10
    )
    assert cli_failure.returncode != 0
    assert (tmp_path / "stopped").exists()
    assert "secret-password" not in cli_failure.stderr
    assert (tmp_path / "playwright-cli-calls").read_text().splitlines() == [
        "-s=chatgpt-auth-recovery attach --cdp=http://127.0.0.1:9222",
    ]

    missing_credentials = dict(env)
    missing_credentials.pop("CHATGPT_EMAIL")
    missing_credentials.pop("CHATGPT_PASSWORD")
    missing = subprocess.run(
        ["bash", str(helper)], env={**missing_credentials, "FAKE_MODE": "success"}, capture_output=True, text=True, timeout=10
    )
    assert missing.returncode != 0
    assert "credentials" in missing.stderr.lower()

    for mode in ("invalid-status", "invalid-output"):
        invalid = subprocess.run(
            ["bash", str(helper)], env={**env, "FAKE_MODE": mode}, capture_output=True, text=True, timeout=10
        )
        assert invalid.returncode != 0
        assert not (tmp_path / "gh-call").exists()
