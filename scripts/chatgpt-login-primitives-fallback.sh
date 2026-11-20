#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

auth_file="$RUNNER_TEMP/primitives-auth.json"
stderr_file="$RUNNER_TEMP/primitives-login.stderr"
status_file="$RUNNER_TEMP/primitives-status.json"
step_file="$RUNNER_TEMP/primitives-step.json"
start_pid=""

wait_for_start() {
  local attempts=${1:-10}
  local attempt
  for ((attempt = 1; attempt <= attempts; attempt++)); do
    if ! kill -0 "$start_pid" 2> /dev/null || [[ "$(ps -o stat= -p "$start_pid" 2> /dev/null)" == *Z* ]]; then
      wait "$start_pid" 2> /dev/null
      return 0
    fi
    sleep 1
  done
  return 1
}

process_group_alive() {
  [[ -n "$start_pid" ]] && kill -0 -- "-$start_pid" 2> /dev/null
}

redact() {
  local value=${1:-}
  for secret_name in CHATGPT_EMAIL CHATGPT_PASSWORD CHATGPT_2FA_KEY CHATGPT_TOTP_KEY OPENAI_USERNAME OPENAI_PASSWORD OPENAI_2FA_KEY; do
    local secret=${!secret_name:-}
    if [[ -n "$secret" ]]; then
      value=${value//"$secret"/[redacted]}
    fi
  done
  printf '%s\n' "$value" | sed -E 's/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/[redacted email]/Ig; s/\b[0-9]{6}\b/[redacted code]/g'
}

diagnostics() {
  local stop_token
  stop_token="$(openssl rand -hex 32)"
  : "${stop_token:?stop-commands fence token generation failed}"
  printf '::stop-commands::%s\n' "$stop_token"
  if [[ -s "$stderr_file" ]]; then
    redact "$(< "$stderr_file")" >&2
  fi
  if [[ -s "$status_file" ]]; then
    redact "$(< "$status_file")" >&2
  fi
  if [[ -s "$step_file" ]]; then
    redact "$(< "$step_file")" >&2
  fi
  printf '::%s::\n' "$stop_token"
}

cleanup() {
  set +e
  uv run scripts/chatgpt-login.py stop > /dev/null 2>&1
  if [[ -n "$start_pid" ]] && { kill -0 "$start_pid" 2> /dev/null || process_group_alive; }; then
    if ! wait_for_start 5; then
      kill -TERM -- "-$start_pid" 2> /dev/null
      if ! wait_for_start 5; then
        kill -KILL -- "-$start_pid" 2> /dev/null
      fi
    fi
    if process_group_alive; then
      kill -KILL -- "-$start_pid" 2> /dev/null
    fi
    wait "$start_pid" 2> /dev/null
  fi
  rm -f "$auth_file" "$stderr_file" "$status_file" "$step_file"
}
trap cleanup EXIT INT TERM

rm -f "$auth_file" "$stderr_file" "$status_file" "$step_file"
setsid uv run scripts/chatgpt-login.py start > "$auth_file" 2> "$stderr_file" &
start_pid=$!

for ((attempt = 1; attempt <= 60; attempt++)); do
  if [[ -s "$auth_file" ]]; then
    break
  fi

  set +e
  uv run scripts/chatgpt-login.py status > "$status_file" 2>&1
  status=$?
  set -e
  if ((status > 2)); then
    printf 'Primitives status failed with non-retryable exit code %d\n' "$status" >&2
    diagnostics
    exit "$status"
  fi

  set +e
  uv run scripts/chatgpt-login.py step > "$step_file" 2>&1
  step_status=$?
  set -e
  if ((step_status > 2)); then
    printf 'Primitives step failed with non-retryable exit code %d\n' "$step_status" >&2
    diagnostics
    exit "$step_status"
  fi

  if [[ -s "$auth_file" ]]; then
    break
  fi
  sleep 5
done

if [[ ! -s "$auth_file" ]]; then
  printf 'Primitives login did not produce an OAuth credential after 60 attempts\n' >&2
  diagnostics
  exit 1
fi

set +e
wait "$start_pid"
start_status=$?
set -e
if ((start_status != 0)); then
  printf 'Primitives login exited with status %d\n' "$start_status" >&2
  diagnostics
  exit "$start_status"
fi

auth_json="$(< "$auth_file")"
printf '%s\n' "$auth_json" | jq -e '.openai.type == "oauth" and .openai.access != "" and .openai.refresh != "" and (.openai.expires | type == "number")' > /dev/null
printf '::add-mask::%s\n' "$auth_json"
GH_TOKEN="$BACLUC_AGENT_GITHUB_TOKEN" gh secret set OPENCODE_AUTH_JSON --repo "$GITHUB_REPOSITORY" --body "$auth_json"
printf 'Auth refreshed via primitives fallback\n'
