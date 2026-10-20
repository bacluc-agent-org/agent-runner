#!/usr/bin/env bash
# shortcut: the wall-clock deadline only widens the retry window for fast failures
# (e.g. ServiceUnavailableError: Location plugins did not finish activating); the root
# cause stays with agent-todo#16.
set -Eeuo pipefail

deadline_seconds="${1:-300}"
out="$(mktemp "${RUNNER_TEMP:-/tmp}/wait_for_opencode.XXXXXX")"

start=$SECONDS
attempt=0
sleep_seconds=2
while true; do
  attempt=$((attempt + 1))
  if timeout 30 opencode models > "$out" 2>&1; then
    echo "OpenCode CLI ready after $attempt attempt(s)"
    exit 0
  fi
  if ((SECONDS - start >= deadline_seconds)); then
    break
  fi
  sleep "$sleep_seconds"
  if ((sleep_seconds < 15)); then
    sleep_seconds=$((sleep_seconds * 2))
    if ((sleep_seconds > 15)); then
      sleep_seconds=15
    fi
  fi
done
echo "OpenCode CLI did not become ready within ${deadline_seconds}s after $attempt attempt(s); last probe output:" >&2
cat "$out"
exit 1
