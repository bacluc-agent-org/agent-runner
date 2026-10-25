import http.client
import json
import os
from pathlib import Path
import re


_request = http.client.HTTPConnection.request


def guarded_request(self, method, url, *args, **kwargs):
    method = method.upper()
    host = (self.host or "").lower().rstrip(".")
    path = url.split("?", 1)[0]
    repository = re.match(r"^/repos/([^/]+)/([A-Za-z0-9_.-]+)(?:/|$)", path, re.IGNORECASE)
    owned = (
        host in {"github.com", "api.github.com"}
        and repository is not None
        and repository.group(1).lower() in {"bacluc", "bacluc-agent"}
        and repository.group(2) not in {".", ".."}
    )
    if method in {"POST", "PUT", "PATCH", "DELETE"} and not owned:
        if method == "POST":
            log_path = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "http-guard.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"method": method, "host": host, "path": path, "decision": "deny"}) + "\n")
        raise PermissionError(f"HTTP guard denied {method} to outsider {host}")
    return _request(self, method, url, *args, **kwargs)


http.client.HTTPConnection.request = guarded_request