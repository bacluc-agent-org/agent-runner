#!/usr/bin/env bash
# shortcut: the wall-clock deadline only widens the retry window for fast failures
# (e.g. ServiceUnavailableError: Location plugins did not finish activating); the root
# cause stays with agent-todo#16. On failure, print npm-install errors from the service
# log so a broken plugin install is visible without dumping the log (it also holds prompts).
set -Eeuo pipefail

deadline_seconds="${1:-600}"
out="$(mktemp "${RUNNER_TEMP:-/tmp}/wait_for_opencode.XXXXXX")"
plugin_log="${XDG_DATA_HOME:-$HOME/.local/share}/opencode/log/opencode.log"

start=$SECONDS
attempt=0
sleep_seconds=2
echo "Waiting for OpenCode CLI up to ${deadline_seconds}s (plugin install can take minutes on a cold cache)"
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
# Only npm-install failures: the service log also contains agent prompts and commands.
if [[ -f $plugin_log ]]; then
  install_errors="$(tail -n 2000 "$plugin_log" | grep -a -E 'message="failed to load plugin".*NpmInstallFailedError' | tail -n 3 | cut -b1-400 || true)"
  if [[ -n $install_errors ]]; then
    echo "OpenCode plugin install errors from ${plugin_log}:" >&2
    printf '%s\n' "$install_errors" >&2
  fi
fi
exit 1
