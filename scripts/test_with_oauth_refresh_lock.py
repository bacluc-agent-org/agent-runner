import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "with_oauth_refresh_lock", Path(__file__).with_name("with-oauth-refresh-lock.py")
)
assert spec and spec.loader
lock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lock)


def test_request_accepts_empty_success_response(monkeypatch):
    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b""

    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(lock.urllib.request, "urlopen", lambda request: Response())

    assert lock.request("DELETE", "/lock") == (204, None)


def test_acquire_removes_stale_lock_ref(monkeypatch, tmp_path):
    calls = []
    responses = iter(
        [
            (200, {"tree": {"sha": "tree-sha"}}),
            (201, {"sha": "new-lock-sha"}),
            (422, None),
            (200, {"object": {"sha": "old-lock-sha"}}),
            (200, {"author": {"date": "2020-01-01T00:00:00Z"}}),
            (204, None),
            (201, None),
        ]
    )

    def request(method, path, body=None):
        calls.append((method, path, body))
        return next(responses)

    monkeypatch.setenv("GITHUB_SHA", "base-sha")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repository")
    monkeypatch.setattr(lock, "request", request)
    monkeypatch.setattr(lock.time, "sleep", lambda seconds: None)

    lock.acquire()

    assert ("DELETE", "/repos/owner/repository/git/refs/heads/oauth-refresh-lock", None) in calls
