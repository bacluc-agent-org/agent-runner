import importlib.util
import json
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
    def __init__(self, content):
        self._content = content

    def content(self):
        return self._content

    def title(self):
        return ""

    def locator(self, selector):
        return _FakeLocator()


def test_has_blocking_screen_detects_cloudflare_interstitial():
    assert chatgpt_login._has_blocking_screen(_FakePage(_CLOUDFLARE_BODY)) == (
        "cloudflare challenge detected: just a moment"
    )


def test_has_blocking_screen_returns_none_for_normal_page():
    page = _FakePage("<html><body><h1>Sign in</h1></body></html>")
    assert chatgpt_login._has_blocking_screen(page) is None


class _RecordingLocator:
    def __init__(self):
        self.fills = []
        self.typed = []

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return True

    def fill(self, value):
        self.fills.append(value)

    def press_sequentially(self, value, delay=None):
        self.typed.append((value, delay))


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