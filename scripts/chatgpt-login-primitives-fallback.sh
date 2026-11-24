#!/usr/bin/env bash
set -Eeuo pipefail

echo "Agent fallback: attempting login via primitives..."
stop_token="$(openssl rand -hex 32)"
: "${stop_token:?stop-commands fence token generation failed}"
printf '::stop-commands::%s\n' "$stop_token"
uv run scripts/chatgpt-login.py start > "$RUNNER_TEMP/primitives-auth.json" 2> "$RUNNER_TEMP/primitives-start.stderr" &
start_pid=$!
cleanup() {
  uv run scripts/chatgpt-login.py stop > /dev/null 2>&1 || true
  kill "$start_pid" 2> /dev/null || true
  wait "$start_pid" 2> /dev/null || true
}
trap cleanup EXIT
completed=false
status_failures=0
for ((attempt = 0; attempt < 120; attempt++)); do
  if ! jobs -pr | grep -qx "$start_pid"; then
    wait "$start_pid" && completed=true
    break
  fi
  status=$(uv run scripts/chatgpt-login.py status) || {
    status_failures=$((status_failures + 1))
    if ((status_failures >= 10)); then
      printf 'Primitives status failed after 10 startup retries.\n' >&2
      exit 1
    fi
    sleep 2
    continue
  }
  status_failures=0
  blocking=$(jq -r '.blocking // empty' <<< "$status")
  if [[ -n "$blocking" ]]; then
    printf 'Login blocked: %s\n' "$blocking" >&2
    exit 1
  fi
  uv run scripts/chatgpt-login.py step || {
    printf 'Primitives step failed; stopping fallback.\n' >&2
    exit 1
  }
  sleep 5
done
if [[ "$completed" != true ]]; then
  printf 'Primitives fallback timed out after 10 minutes.\n' >&2
  exit 1
fi
printf '::%s::\n' "$stop_token"
auth_json=$(cat "$RUNNER_TEMP/primitives-auth.json")
printf '%s\n' "$auth_json" | jq -e '.openai.type == "oauth" and .openai.access != "" and .openai.refresh != "" and (.openai.expires | type == "number")' > /dev/null
[[ -n "${auth_json:-}" ]] && printf '::add-mask::%s\n' "$auth_json"
GH_TOKEN="$BACLUC_AGENT_GITHUB_TOKEN" gh secret set OPENCODE_AUTH_JSON --repo "$GITHUB_REPOSITORY" --body "$auth_json"
printf 'Agent primitives fallback succeeded\n'
