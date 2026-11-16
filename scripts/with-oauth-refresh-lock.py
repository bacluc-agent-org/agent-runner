#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from calendar import timegm


LOCK_REF = "refs/heads/oauth-refresh-lock"
LEASE_SECONDS = 900
POLL_SECONDS = 5
lock_sha = None


def lock_file():
    return os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "chatgpt-oauth-refresh-lock")


def request(method, path, body=None):
    token = os.environ.get("BACLUC_AGENT_GITHUB_TOKEN") or os.environ["GITHUB_TOKEN"]
    url = f'{os.environ.get("GITHUB_API_URL", "https://api.github.com")}{path}'
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read()
            return response.status, None if not payload else json.loads(payload)
    except urllib.error.HTTPError as error:
        return error.code, None


def repository_path():
    return f'/repos/{os.environ["GITHUB_REPOSITORY"]}'


def acquire():
    global lock_sha
    status, base = request("GET", f'{repository_path()}/git/commits/{os.environ["GITHUB_SHA"]}')
    if status != 200:
        raise RuntimeError(f"could not inspect OAuth refresh lock base: HTTP {status}")
    status, commit = request(
        "POST",
        f"{repository_path()}/git/commits",
        {
            "message": f"OAuth refresh lock {uuid.uuid4().hex}",
            "tree": base["tree"]["sha"],
            "parents": [os.environ["GITHUB_SHA"]],
        },
    )
    if status != 201:
        raise RuntimeError(f"could not create OAuth refresh lock commit: HTTP {status}")
    lock_sha = commit["sha"]
    with open(lock_file(), "w", encoding="ascii") as handle:
        handle.write(lock_sha)
    while True:
        status, _ = request(
            "POST",
            f"{repository_path()}/git/refs",
            {"ref": LOCK_REF, "sha": lock_sha},
        )
        if status == 201:
            return
        if status != 422:
            raise RuntimeError(f"could not acquire OAuth refresh lock: HTTP {status}")
        status, lock = request("GET", f"{repository_path()}/git/ref/heads/oauth-refresh-lock")
        if status != 200:
            time.sleep(POLL_SECONDS)
            continue
        commit_status, commit = request("GET", f'{repository_path()}/git/commits/{lock["object"]["sha"]}')
        if commit_status == 200:
            created = commit["author"]["date"]
            age = time.time() - timegm(time.strptime(created, "%Y-%m-%dT%H:%M:%SZ"))
            if age > LEASE_SECONDS:
                request("DELETE", f"{repository_path()}/git/refs/heads/oauth-refresh-lock")
        time.sleep(POLL_SECONDS)


def release():
    owned_sha = lock_sha
    if owned_sha is None:
        try:
            with open(lock_file(), encoding="ascii") as handle:
                owned_sha = handle.read().strip()
        except FileNotFoundError:
            return
    if not owned_sha:
        return
    status, lock = request("GET", f"{repository_path()}/git/ref/heads/oauth-refresh-lock")
    if status == 200 and lock["object"]["sha"] == owned_sha:
        request("DELETE", f"{repository_path()}/git/refs/heads/oauth-refresh-lock")
    try:
        os.remove(lock_file())
    except FileNotFoundError:
        pass


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--acquire":
        acquire()
        return
    if len(sys.argv) == 2 and sys.argv[1] == "--release":
        release()
        return
    if len(sys.argv) < 2 or sys.argv[1] != "--":
        raise SystemExit("usage: with-oauth-refresh-lock.py [--acquire|--release|-- command [args...]]")
    acquire()
    try:
        raise SystemExit(subprocess.call(sys.argv[2:]))
    finally:
        release()


if __name__ == "__main__":
    main()
