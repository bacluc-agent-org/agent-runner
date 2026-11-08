#!/usr/bin/env bash
set -Eeuo pipefail

auth_file="$RUNNER_TEMP/auth.json"
stderr_file="$RUNNER_TEMP/primitives-login.stderr"
if [[ -z "${OPENAI_USERNAME:-}" && -z "${CHATGPT_EMAIL:-}" ]] || [[ -z "${OPENAI_PASSWORD:-}" && -z "${CHATGPT_PASSWORD:-}" ]]; then
  printf 'Primitive login credentials are not configured; set an email and password secret\n' >&2
  exit 1
fi
uv run scripts/chatgpt-login.py start > "$auth_file" 2> "$stderr_file" &
start_pid=$!

cleanup() {
  uv run scripts/chatgpt-login.py stop > /dev/null 2>&1 || true
  kill "$start_pid" 2> /dev/null || true
  wait "$start_pid" 2> /dev/null || true
}
trap cleanup EXIT

blocking_recovery_attempted=false
recover_blocking_login() {
  local session='chatgpt-auth-recovery'
  local cdp_port="${OPENAI_CDP_PORT:-9222}"
  local snapshot_file="$RUNNER_TEMP/chatgpt-login-recovery.snapshot"
  local attached=false

  cleanup_recovery() {
    if [[ "$attached" == true ]]; then
      playwright-cli "-s=$session" detach > /dev/null 2>&1 || true
      attached=false
    fi
  }
  trap cleanup_recovery RETURN

  if ! playwright-cli "-s=$session" attach "--cdp=http://127.0.0.1:$cdp_port" > /dev/null 2>&1; then
    printf 'Playwright CLI could not attach to the login browser\n' >&2
    return 1
  fi
  attached=true
  if ! playwright-cli "-s=$session" snapshot > "$snapshot_file" 2> /dev/null; then
    printf 'Playwright CLI could not capture the login page\n' >&2
    return 1
  fi
  chmod 600 "$snapshot_file"
  if ! playwright-cli "-s=$session" detach > /dev/null 2>&1; then
    printf 'Playwright CLI could not detach from the login browser\n' >&2
    return 1
  fi
  attached=false
  trap - RETURN
  printf 'Playwright handoff snapshot retained at %s; inspect it and continue with the login status/step primitives\n' "$snapshot_file" >&2
}

for attempt in $(seq 1 120); do
  if ! kill -0 "$start_pid" 2> /dev/null; then
    if ! wait "$start_pid"; then
      printf 'Primitive login startup failed; safe diagnostics:\n' >&2
      cat "$stderr_file" >&2
      exit 1
    fi
    break
  fi

  status_json=''
  if ! status_json=$(uv run scripts/chatgpt-login.py status); then
    if ((attempt >= 10)); then
      printf 'Primitive login status failed repeatedly\n' >&2
      exit 1
    fi
    sleep 2
    continue
  fi
  printf '%s\n' "$status_json" | jq -e 'type == "object" and (.error == null) and (.step | type == "string") and ((.blocking == null) or (.blocking | type == "string"))' > /dev/null || {
    printf 'Primitive login reported invalid status\n' >&2
    exit 1
  }
  if [[ -n "$(jq -r '.blocking // empty' <<< "$status_json")" ]]; then
    if [[ "$blocking_recovery_attempted" == true ]]; then
      printf 'Primitive login remained blocked after Playwright recovery\n' >&2
      exit 1
    fi
    blocking_recovery_attempted=true
    recover_blocking_login || exit 1
    sleep 5
    continue
  fi
  if [[ "$(jq -r '.code_ready // false' <<< "$status_json")" == true ]]; then
    wait "$start_pid"
    break
  fi

  step_json=''
  step_status=0
  step_json=$(uv run scripts/chatgpt-login.py step) || step_status=$?
  if ((step_status != 0)); then
    if ((step_status != 1 && step_status != 2)); then
      printf 'Primitive login step failed with status %s\n' "$step_status" >&2
      exit "$step_status"
    fi
  else
    printf '%s\n' "$step_json" | jq -e 'type == "object" and (.error == null) and (.step | type == "string")' > /dev/null || {
      printf 'Primitive login step returned invalid status\n' >&2
      exit 1
    }
  fi
  sleep 5
done

if kill -0 "$start_pid" 2> /dev/null; then
  printf 'Primitive login timed out after 10 minutes\n' >&2
  exit 1
fi
auth_json=$(cat "$auth_file")
printf '%s\n' "$auth_json" | jq -e '.openai.type == "oauth" and (.openai.access | type == "string" and length > 0) and (.openai.refresh | type == "string" and length > 0) and (.openai.expires | type == "number")' > /dev/null || {
  printf 'Primitive login produced invalid authentication JSON\n' >&2
  exit 1
}
printf '::add-mask::%s\n' "$auth_json"
GH_TOKEN="$BACLUC_AGENT_GITHUB_TOKEN" gh secret set OPENCODE_AUTH_JSON --repo "$GITHUB_REPOSITORY" --body "$auth_json"
printf 'Auth refreshed via primitives fallback\n'
