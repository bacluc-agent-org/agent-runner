import importlib.util
import inspect
import json
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


def test_password_step_pauses_after_fill_and_after_submit(monkeypatch):
    fake_time = _FakeTime()
    monkeypatch.setattr(chatgpt_login, "time", fake_time)
    page = _FillPage()
    assert chatgpt_login._do_password_step(page, "hunter2")
    assert page.locator_instance.typed == [("hunter2", 200)]
    assert fake_time.sleeps == [4.0, 8.0]


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


def test_credential_steps_dump_untrusted_logs_only_on_failure_and_without_credentials():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    primitives = workflow[
        workflow.index("- name: Agent-usable primitives fallback") : workflow.index(
            "- name: Fail unless a path set the secret"
        )
    ]
    lines = [line.strip() for line in primitives.splitlines() if line.strip()]
    dump = [i for i, line in enumerate(lines) if "tail -n 40" in line]
    assert len(dump) == 1
    assert lines[dump[0] - 1 : dump[0] + 2] == [
        "if (( start_status != 0 )); then",
        'grep -v \'"openai"\' "$start_log" | tail -n 40 >&2 || true',
        "fi",
    ]

    assert 'cat "$RUNNER_TEMP/auth.json" >&2' not in workflow
    browser = workflow[
        workflow.index("- name: Browser login (fallback)") : workflow.index(
            "- name: Agent-usable primitives fallback"
        )
    ]
    browser_lines = [line.strip() for line in browser.splitlines() if line.strip()]
    browser_dump = [i for i, line in enumerate(browser_lines) if "$RUNNER_TEMP/auth.json" in line and ">&2" in line]
    assert len(browser_dump) == 1
    assert browser_lines[browser_dump[0]] == (
        "grep -vE '\"openai\"|rt\\.1\\.' \"$RUNNER_TEMP/auth.json\" >&2 || true"
    )


def test_primitives_loop_sleeps_and_continues_when_blocked():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    primitives = workflow[
        workflow.index("- name: Agent-usable primitives fallback") : workflow.index(
            "- name: Fail unless a path set the secret"
        )
    ]
    lines = [line.strip() for line in primitives.splitlines() if line.strip()]
    blocking = [i for i, line in enumerate(lines) if line == "printf 'primitives: blocked: %s\\n' \"$blocking\""]
    assert len(blocking) == 1
    i = blocking[0]
    assert lines[i + 1 : i + 3] == ["sleep 10", "continue"]


_OPENAI_ERROR_BODY = "<html><body><h1>Oops, an error occurred!</h1></body></html>"


def test_validate_step_keeps_default_plugins_and_only_the_chatgpt_model():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    validation = workflow[
        workflow.index("- name: Validate refreshed token with opencode") : workflow.index(
            "- name: Set secret (refreshed token)"
        )
    ]
    assert "OPENCODE_DISABLE_DEFAULT_PLUGINS" not in validation
    assert "openai/gpt-5.6-luna" in validation
    assert "openai/gpt-5.5" not in validation
    assert "openai/gpt-5.4" not in validation
    assert "openai/gpt-5.4-mini" not in validation
    assert "printf '::stop-commands::%s\\n' \"$stop_token\"" in validation


def test_force_fallback_input_gates_the_refresh_step():
    workflow = (
        Path(__file__).parents[1] / ".github/workflows/refresh-chatgpt-auth.yml"
    ).read_text(encoding="utf-8")
    assert "force_fallback:" in workflow
    refresh = workflow[
        workflow.index("- name: Try token refresh (no browser)") : workflow.index(
            "- name: Validate refreshed token with opencode"
        )
    ]
    assert "if: ${{ !inputs.force_fallback }}" in refresh


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


def test_cmd_step_waits_for_blocking_after_successful_password_step(monkeypatch, capsys):
    page = _FakePage("<html><body><h1>Sign in</h1></body></html>", url="https://auth.openai.com/log-in/password")
    _stub_browser(monkeypatch, page)
    _stub_auth_env(monkeypatch)
    monkeypatch.setattr(chatgpt_login, "_do_current_step", lambda *a, **k: True)
    waits = []

    def fake_wait(page, timeout_s=90):
        waits.append(timeout_s)
        return None if timeout_s == 15 else "bot detection in progress"

    monkeypatch.setattr(chatgpt_login, "_wait_for_blocking_clear", fake_wait)
    assert chatgpt_login.cmd_step() == 2
    payload = json.loads(capsys.readouterr().out)
    assert waits == [15, 30]
    assert payload["blocking"] == "bot detection in progress"


def test_full_login_retries_the_password_error_page_before_treating_it_as_blocking():
    source = Path(chatgpt_login.__file__).read_text(encoding="utf-8")
    full_login = source[source.index("def _full_login") : source.index("def main")]
    assert full_login.index("OpenAI error page after password submit") < full_login.index(
        "Login blocked after password"
    )
