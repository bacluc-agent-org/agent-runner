#!/usr/bin/env bash
set -Eeuo pipefail

auth_file="$RUNNER_TEMP/auth.json"
stderr_file="$RUNNER_TEMP/primitives-login.stderr"
[[ -n "${CHATGPT_EMAIL:-${OPENAI_USERNAME:-}}" && -n "${CHATGPT_PASSWORD:-${OPENAI_PASSWORD:-}}" ]] || {
  printf 'Primitive login credentials are not configured\n' >&2
  exit 1
}

uv run scripts/chatgpt-login.py start > "$auth_file" 2> "$stderr_file" &
start_pid=$!
session='chatgpt-auth-recovery'
cdp_port="${OPENAI_CDP_PORT:-9222}"
deadline=$((SECONDS + ${PLAYWRIGHT_HANDOFF_TIMEOUT_SECONDS:-300}))
attached=false
snapshot_file="$RUNNER_TEMP/chatgpt-login-recovery.snapshot"
actions_file="$RUNNER_TEMP/chatgpt-login-recovery.actions"

cleanup() {
  if [[ "$attached" == true ]]; then
    run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" detach > /dev/null 2>&1 || true
  fi
  run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" uv run scripts/chatgpt-login.py stop > /dev/null 2>&1 || true
  kill "$start_pid" > /dev/null 2>&1 || true
  for _ in $(seq 1 10); do
    kill -0 "$start_pid" > /dev/null 2>&1 || {
      wait "$start_pid" > /dev/null 2>&1 || true
      break
    }
    run_bounded 1 sleep 0.1 || true
  done
  if kill -0 "$start_pid" > /dev/null 2>&1; then
    kill -KILL "$start_pid" > /dev/null 2>&1 || true
    wait "$start_pid" > /dev/null 2>&1 || true
  fi
  rm -f -- "$snapshot_file" "$actions_file" "$stderr_file"
}
trap cleanup EXIT

remaining() {
  local value=$((deadline - SECONDS))
  ((value > 0)) || return 1
  printf '%s\n' "$value"
}

run_bounded() {
  local limit="$1"
  shift
  local left
  left=$(remaining) || return 1
  ((limit < left)) && left=$limit
  env -u BACLUC_AGENT_GITHUB_TOKEN -u BRAVE_API_KEY -u CHATGPT_EMAIL -u CHATGPT_PASSWORD \
    -u CHATGPT_TOTP_KEY -u CHATGPT_2FA_KEY -u OPENAI_USERNAME -u OPENAI_PASSWORD \
    -u OPENAI_2FA_KEY -u OPENCODE_AUTH_CONTENT -u GH_TOKEN -u GITHUB_TOKEN \
    timeout "$left" "$@"
}

redact_snapshot() {
  CHATGPT_EMAIL="${CHATGPT_EMAIL:-${OPENAI_USERNAME:-}}" \
    CHATGPT_PASSWORD="${CHATGPT_PASSWORD:-${OPENAI_PASSWORD:-}}" \
    CHATGPT_TOTP_KEY="${CHATGPT_TOTP_KEY:-${CHATGPT_2FA_KEY:-${OPENAI_2FA_KEY:-}}}" \
    python3 -c 'import os, re, sys
text = sys.stdin.read()
for name in ("CHATGPT_EMAIL", "CHATGPT_PASSWORD", "CHATGPT_TOTP_KEY"):
    value = os.environ.get(name)
    if value:
        text = text.replace(value, "[redacted]")
text = re.sub(r"(?i)(authorization|cookie|token|code|password|secret)\s*[:=]\s*[^\s,]+", r"\1=[redacted]", text)
sys.stdout.write(text)'
}

capture_snapshot() {
  local tmp
  tmp=$(mktemp "$snapshot_file.XXXXXX")
  chmod 600 "$tmp"
  run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" snapshot \
    2> /dev/null | redact_snapshot > "$tmp" || {
    rm -f "$tmp"
    return 1
  }
  mv -f -- "$tmp" "$snapshot_file"
}

validate_ref() { [[ "$1" =~ ^e[0-9]+$ ]]; }

run_action() {
  local line="$1" action ref value kind url key
  jq -e 'type == "object" and (.action | type == "string")' <<< "$line" > /dev/null || return 1
  action=$(jq -r '.action' <<< "$line")
  case "$action" in
    snapshot)
      jq -e 'length == 1' <<< "$line" > /dev/null || return 1
      capture_snapshot
      ;;
    click | hover | check | uncheck | fill_secret | otp)
      ref=$(jq -r '.ref // empty' <<< "$line")
      validate_ref "$ref" || return 1
      case "$action" in
        fill_secret)
          kind=$(jq -r '.kind // empty' <<< "$line")
          [[ "$kind" == email || "$kind" == password ]] || return 1
          jq -e 'length == 3' <<< "$line" > /dev/null || return 1
          value="${CHATGPT_EMAIL:-${OPENAI_USERNAME:-}}"
          [[ "$kind" == password ]] && value="${CHATGPT_PASSWORD:-${OPENAI_PASSWORD:-}}"
          run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" fill "$ref" "$value" > /dev/null 2>&1
          ;;
        otp)
          jq -e 'length == 2' <<< "$line" > /dev/null || return 1
          value=$(CHATGPT_TOTP_KEY="${CHATGPT_TOTP_KEY:-${CHATGPT_2FA_KEY:-${OPENAI_2FA_KEY:-}}}" \
            uv run python -c 'import os, pyotp; print(pyotp.TOTP(os.environ["CHATGPT_TOTP_KEY"]).now())')
          [[ "$value" =~ ^[0-9]{6}$ ]] || return 1
          run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" fill "$ref" "$value" > /dev/null 2>&1
          ;;
        *)
          jq -e 'length == 2' <<< "$line" > /dev/null || return 1
          run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" "$action" "$ref" > /dev/null 2>&1
          ;;
      esac
      ;;
    type_secret)
      ref=$(jq -r '.ref // empty' <<< "$line")
      kind=$(jq -r '.kind // empty' <<< "$line")
      validate_ref "$ref" && [[ "$kind" == email || "$kind" == password ]] || return 1
      jq -e 'length == 3' <<< "$line" > /dev/null || return 1
      value="${CHATGPT_EMAIL:-${OPENAI_USERNAME:-}}"
      [[ "$kind" == password ]] && value="${CHATGPT_PASSWORD:-${OPENAI_PASSWORD:-}}"
      run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" type "$ref" "$value" > /dev/null 2>&1
      ;;
    goto)
      url=$(jq -r '.url // empty' <<< "$line")
      local auth_url_re='^https://(auth\.openai\.com|chatgpt\.com)(/[^?#]*)?$'
      [[ "$url" =~ $auth_url_re ]] || return 1
      jq -e 'length == 2' <<< "$line" > /dev/null || return 1
      run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" goto "$url" > /dev/null 2>&1
      ;;
    press)
      key=$(jq -r '.key // empty' <<< "$line")
      local key_re='^[A-Za-z0-9_ -]{1,32}$'
      [[ "$key" =~ $key_re ]] || return 1
      jq -e 'length == 2' <<< "$line" > /dev/null || return 1
      run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" press "$key" > /dev/null 2>&1
      ;;
    status)
      jq -e 'length == 1' <<< "$line" > /dev/null || return 1
      ;;
    *) return 1 ;;
  esac
}

recover() {
  local count=0 line
  run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" playwright-cli "-s=$session" attach \
    --cdp="http://127.0.0.1:$cdp_port" > /dev/null 2>&1 || return 1
  attached=true
  while remaining > /dev/null; do
    capture_snapshot || return 1
    : > "$actions_file"
    local prompt="Drive the attached authentication page. Read only $snapshot_file and write JSONL actions to $actions_file. Each line must be one action object: snapshot; click/hover/check/uncheck with ref eN; fill_secret or type_secret with ref and kind email|password; otp with ref; goto with a URL on https://auth.openai.com or https://chatgpt.com without query or fragment; press with a short key; status. Never request or print secrets. Adapt to the current snapshot, use at most 32 actions, and do not call tools or modify other files."
    if ! run_bounded "${PLAYWRIGHT_AGENT_TIMEOUT_SECONDS:-120}" opencode run --auto \
      --model "${PLAYWRIGHT_AGENT_MODEL:-opencode-go-openai/gpt-5.6-luna}" \
      --title chatgpt-auth-recovery "$prompt" > /dev/null 2>&1; then
      continue
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
      count=$((count + 1))
      ((count <= 32)) || return 1
      run_action "$line" || return 1
      capture_snapshot || return 1
    done < "$actions_file"
    status_json=$(run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" uv run scripts/chatgpt-login.py status) || return 1
    jq -e 'type == "object" and (.error == null) and (.step | type == "string")' <<< "$status_json" > /dev/null || return 1
    [[ "$(jq -r '.code_ready // false' <<< "$status_json")" == true ]] && return 0
  done
  return 1
}

for _ in $(seq 1 120); do
  remaining > /dev/null || break
  if ! kill -0 "$start_pid" > /dev/null 2>&1; then
    wait "$start_pid" || {
      printf 'Primitive login startup failed\n' >&2
      exit 1
    }
    break
  fi
  status_json=$(run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" uv run scripts/chatgpt-login.py status 2> /dev/null) || status_json=''
  if [[ -n "$status_json" ]] && jq -e 'type == "object" and (.error == null) and (.step | type == "string")' <<< "$status_json" > /dev/null; then
    if [[ -n "$(jq -r '.blocking // empty' <<< "$status_json")" ]]; then
      recover || {
        printf 'Playwright authentication recovery failed\n' >&2
        exit 1
      }
    elif [[ "$(jq -r '.code_ready // false' <<< "$status_json")" == true ]]; then
      wait "$start_pid"
      break
    else
      run_bounded "${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}" uv run scripts/chatgpt-login.py step > /dev/null 2>&1 || true
    fi
  fi
  run_bounded 1 sleep 1 || true
done

kill -0 "$start_pid" > /dev/null 2>&1 && {
  printf 'Primitive login timed out\n' >&2
  exit 1
}
auth_json=$(cat "$auth_file")
printf '%s\n' "$auth_json" | jq -e '.openai.type == "oauth" and (.openai.access | type == "string" and length > 0) and (.openai.refresh | type == "string" and length > 0) and (.openai.expires | type == "number")' > /dev/null || {
  printf 'Primitive login produced invalid authentication JSON\n' >&2
  exit 1
}
printf '::add-mask::%s\n' "$auth_json"
GH_TOKEN="$BACLUC_AGENT_GITHUB_TOKEN" gh secret set OPENCODE_AUTH_JSON --repo "$GITHUB_REPOSITORY" --body "$auth_json"
printf 'Auth refreshed via primitives fallback\n'
