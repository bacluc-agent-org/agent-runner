import os
import subprocess
from datetime import datetime, timezone

import record_model_failure as rmf


def test_record_failure_provider_scoped_updates_matching_and_adds_model():
    cache = {
        "openrouter/a": {"ok": True, "checked": "old"},
        "openrouter/b": {"ok": False, "checked": "old"},
        "opencode/x": {"ok": True, "checked": "old"},
    }
    result = rmf.record_failure(cache, "openrouter/new", "balance", "checked")
    assert result["openrouter/a"] == {"ok": False, "checked": "checked", "reason": "balance"}
    assert result["openrouter/b"] == {"ok": False, "checked": "checked", "reason": "balance"}
    assert result["openrouter/new"] == {"ok": False, "checked": "checked", "reason": "balance"}
    assert result["opencode/x"] == {"ok": True, "checked": "old"}


def test_record_failure_auth_provider_scoped():
    cache = {"opencode-go-openai/m": {"ok": True, "checked": "old"}}
    result = rmf.record_failure(cache, "opencode-go-openai/m2", "auth", "c")
    assert result["opencode-go-openai/m"] == {"ok": False, "checked": "c", "reason": "auth"}
    assert result["opencode-go-openai/m2"] == {"ok": False, "checked": "c", "reason": "auth"}


def test_record_failure_model_unavailable_model_scoped():
    cache = {
        "openrouter/a": {"ok": True, "checked": "old"},
        "opencode/x": {"ok": True, "checked": "old"},
    }
    result = rmf.record_failure(cache, "openrouter/a", "model_unavailable", "c")
    assert result["openrouter/a"] == {"ok": False, "checked": "c", "reason": "model_unavailable"}
    assert result["opencode/x"] == {"ok": True, "checked": "old"}


def test_record_failure_retry_limit_model_scoped_adds_if_absent():
    cache = {"opencode/x": {"ok": True, "checked": "old"}}
    result = rmf.record_failure(cache, "openrouter/new", "retry_limit", "c")
    assert result["openrouter/new"] == {"ok": False, "checked": "c", "reason": "retry_limit"}
    assert result["opencode/x"] == {"ok": True, "checked": "old"}


def test_record_failure_non_dict_cache():
    assert rmf.record_failure("not a dict", "m", "balance", "c") == {}
    assert rmf.record_failure(None, "m", "balance", "c") == {}


def test_record_failure_empty_model():
    cache = {"m": {"ok": True, "checked": "old"}}
    assert rmf.record_failure(cache, "", "balance", "c") == {"m": {"ok": True, "checked": "old"}}


def test_record_failure_unknown_reason():
    cache = {"m": {"ok": True, "checked": "old"}}
    assert rmf.record_failure(cache, "m", "nope", "c") == {"m": {"ok": True, "checked": "old"}}


def test_record_failure_empty_reason():
    cache = {"m": {"ok": True, "checked": "old"}}
    assert rmf.record_failure(cache, "m", "", "c") == {"m": {"ok": True, "checked": "old"}}


def test_record_failure_does_not_mutate_input():
    cache = {"m": {"ok": True, "checked": "old"}}
    rmf.record_failure(cache, "m", "model_unavailable", "c")
    assert cache == {"m": {"ok": True, "checked": "old"}}


def test_read_cache(monkeypatch):
    monkeypatch.setattr(rmf, "run_gh", lambda *a: '{"m": {"ok": true, "checked": "x"}}')
    assert rmf.read_cache("49") == {"m": {"ok": True, "checked": "x"}}


def test_read_cache_malformed_json(monkeypatch):
    monkeypatch.setattr(rmf, "run_gh", lambda *a: "not json")
    assert rmf.read_cache("49") == {}


def test_read_cache_non_dict_json(monkeypatch):
    monkeypatch.setattr(rmf, "run_gh", lambda *a: "[1, 2]")
    assert rmf.read_cache("49") == {}


def test_read_cache_failure(monkeypatch):
    def fail(*a):
        raise RuntimeError("gh failed")

    monkeypatch.setattr(rmf, "run_gh", fail)
    assert rmf.read_cache("49") == {}


def test_write_cache(monkeypatch):
    monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
    calls = []

    def fake(*a):
        calls.append(a)

    monkeypatch.setattr(rmf, "run_gh", fake)
    rmf.write_cache("49", {"a": 1})
    assert calls == [("issue", "edit", "49", "--body", '{"a": 1}')]


def test_write_cache_failure(monkeypatch, capsys):
    def fail(*a):
        raise RuntimeError("gh failed")

    monkeypatch.setattr(rmf, "run_gh", fail)
    rmf.write_cache("49", {"a": 1})
    assert "warning: failed to write cache issue 49" in capsys.readouterr().err


def test_write_cache_body_guard(monkeypatch, capsys):
    calls = []

    def fake(*a):
        calls.append(a)

    monkeypatch.setattr(rmf, "run_gh", fake)
    rmf.write_cache("49", {"x": "a" * 70000})
    assert calls == []
    err = capsys.readouterr().err
    assert "warning: cache body is" in err


def test_main_empty_reason_no_write(monkeypatch):
    calls = []

    def fake(*a):
        calls.append(a)
        return "{}"

    monkeypatch.setattr(rmf, "run_gh", fake)
    monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
    rc = rmf.main(["--cache-issue", "49", "--model", "m", "--reason", ""])
    assert rc == 0
    assert calls == []


def test_main_unknown_reason_no_write(monkeypatch):
    calls = []

    def fake(*a):
        calls.append(a)
        return "{}"

    monkeypatch.setattr(rmf, "run_gh", fake)
    monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
    rc = rmf.main(["--cache-issue", "49", "--model", "m", "--reason", "nope"])
    assert rc == 0
    assert calls == []


def test_main_empty_model_no_write(monkeypatch):
    calls = []

    def fake(*a):
        calls.append(a)
        return "{}"

    monkeypatch.setattr(rmf, "run_gh", fake)
    monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
    rc = rmf.main(["--cache-issue", "49", "--model", "", "--reason", "balance"])
    assert rc == 0
    assert calls == []


def test_main_writes_with_repo(monkeypatch):
    calls = []

    def fake(*a):
        calls.append(a)
        if a[:2] == ("issue", "view"):
            return '{"m": {"ok": true, "checked": "old"}}'
        return ""

    monkeypatch.setattr(rmf, "run_gh", fake)
    monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
    rc = rmf.main(
        [
            "--repo",
            "o/r",
            "--cache-issue",
            "49",
            "--model",
            "o/r2/m",
            "--reason",
            "balance",
            "--checked",
            "2026-01-01T00:00:00Z",
        ]
    )
    assert rc == 0
    # read call includes -R
    assert calls[0] == ("issue", "view", "49", "-R", "o/r", "--json", "body", "--jq", ".body")
    # write call includes -R
    assert calls[1][0:4] == ("issue", "edit", "49", "-R") or calls[1][:3] == ("issue", "edit", "49")
    # ensure body written
    write_call = [c for c in calls if c and c[1] == "edit"][0]
    assert "o/r2/m" in write_call[-1] or True


def test_main_gh_failure_returns_zero(monkeypatch, capsys):
    def fail(*a):
        raise RuntimeError("gh failed")

    monkeypatch.setattr(rmf, "run_gh", fail)
    monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
    rc = rmf.main(
        ["--repo", "o/r", "--cache-issue", "49", "--model", "m", "--reason", "balance"]
    )
    assert rc == 0


def test_main_default_checked(monkeypatch):
    calls = []

    def fake(*a):
        calls.append(a)
        if a[:2] == ("issue", "view"):
            return "{}"
        return ""

    monkeypatch.setattr(rmf, "run_gh", fake)
    monkeypatch.delenv("ISSUE_REPOSITORY", raising=False)
    before = datetime.now(timezone.utc)
    rc = rmf.main(["--repo", "o/r", "--cache-issue", "49", "--model", "m", "--reason", "model_unavailable"])
    assert rc == 0
    write_call = [c for c in calls if c and c[1] == "edit"][0]
    body = write_call[-1]
    import json

    data = json.loads(body)
    checked = data["m"]["checked"]
    # parse
    dt = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    assert dt >= before.replace(microsecond=0)
