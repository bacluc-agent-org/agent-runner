#!/usr/bin/env bash
set -Eeuo pipefail

attempts="${1:-15}"
out="$(mktemp "${RUNNER_TEMP:-/tmp}/wait_for_opencode.XXXXXX")"

for i in $(seq 1 "$attempts"); do
  if timeout 30 opencode models > "$out" 2>&1; then
    echo "OpenCode CLI ready after $i attempt(s)"
    exit 0
  fi
  sleep 2
done
echo "OpenCode CLI did not become ready after $attempts attempts; last probe output:" >&2
cat "$out"
exit 1
