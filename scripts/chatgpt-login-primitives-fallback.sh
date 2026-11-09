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
  local handoff_file="$RUNNER_TEMP/chatgpt-login-recovery.handoff"
  local command_file="$RUNNER_TEMP/chatgpt-login-recovery.commands"
  local snapshot_tmp=''
  local handoff_tmp=''
  local command_tmp=''
  local attached=false
  local handoff_timeout="${PLAYWRIGHT_HANDOFF_TIMEOUT_SECONDS:-300}"
  local command_timeout="${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}"
  local agent_timeout="${PLAYWRIGHT_AGENT_TIMEOUT_SECONDS:-120}"
  local agent_model="${PLAYWRIGHT_AGENT_MODEL:-opencode-go-openai/gpt-5.6-luna}"

  if [[ ! "$handoff_timeout" =~ ^[1-9][0-9]*$ || "$handoff_timeout" -gt 300 || ! "$command_timeout" =~ ^[1-9][0-9]*$ || "$command_timeout" -gt 30 || ! "$agent_timeout" =~ ^[1-9][0-9]*$ || "$agent_timeout" -gt 300 ]]; then
    printf 'Invalid Playwright handoff timeout\n' >&2
    return 1
  fi

  local deadline=$((SECONDS + handoff_timeout))

  run_with_deadline() {
    local max_timeout="$1"
    shift
    local remaining=$((deadline - SECONDS))
    ((remaining > 0)) || return 1
    local operation_timeout="$max_timeout"
    ((operation_timeout > remaining)) && operation_timeout=$remaining
    timeout "$operation_timeout" "$@"
  }

  cleanup_recovery() {
    if [[ "$attached" == true ]]; then
      run_with_deadline "$handoff_timeout" playwright-cli "-s=$session" detach > /dev/null 2>&1 || true
      attached=false
    fi
    rm -f -- "$snapshot_tmp" "$handoff_tmp" "$command_tmp" "$snapshot_file" "$handoff_file" "$command_file"
  }
  trap cleanup_recovery RETURN
  rm -f -- "$snapshot_file" "$handoff_file" "$command_file"
  snapshot_tmp=$(mktemp "$snapshot_file.XXXXXX")
  handoff_tmp=$(mktemp "$handoff_file.XXXXXX")
  command_tmp=$(mktemp "$command_file.XXXXXX")
  chmod 600 "$snapshot_tmp" "$handoff_tmp" "$command_tmp"
  mv -f -- "$command_tmp" "$command_file"
  command_tmp=''

  if ! run_with_deadline "$handoff_timeout" playwright-cli "-s=$session" attach "--cdp=http://127.0.0.1:$cdp_port" > /dev/null 2>&1; then
    printf 'Playwright CLI could not attach to the login browser\n' >&2
    return 1
  fi
  attached=true
  cat > "$handoff_tmp" << EOF
session=$session
cdp=http://127.0.0.1:$cdp_port
snapshot=$snapshot_file
attach=playwright-cli -s=$session attach --cdp=http://127.0.0.1:$cdp_port
inspect=playwright-cli -s=$session snapshot
status=uv run scripts/chatgpt-login.py status
step=uv run scripts/chatgpt-login.py step
detach=playwright-cli -s=$session detach
commands=$command_file
operations=snapshot|click|fill_secret|type_secret|otp|goto|press|check|uncheck|hover|status
timeout_seconds=$handoff_timeout
command_timeout_seconds=$command_timeout
EOF
  local recovery_prompt
  recovery_prompt=$(
    cat << EOF
Drive the blocked ChatGPT login through the attached Playwright session.
Read only the snapshot at $snapshot_file. Write only newline-delimited commands to $command_file, then exit.
Allowed commands are exactly: snapshot, click eN, fill_secret eN email|password, type_secret eN email|password, otp eN, goto https://auth.openai.com/PATH, goto https://chatgpt.com/PATH, press WORD, check eN, uncheck eN, hover eN, status.
Use symbolic secret commands for credentials and otp for TOTP; never read, print, or write credential values. Use only refs from the snapshot and safe auth URLs without query strings or fragments. Keep the batch at most 32 commands. Do not call playwright-cli directly, modify any other file, or report secrets.
EOF
  )
  local handoff_ready=false
  while ((SECONDS < deadline)); do
    run_agent_with_deadline() {
      local max_timeout="$1"
      shift
      local remaining=$((deadline - SECONDS))
      ((remaining > 0)) || return 1
      local operation_timeout="$max_timeout"
      ((operation_timeout > remaining)) && operation_timeout=$remaining
      env -u BACLUC_AGENT_GITHUB_TOKEN -u BRAVE_API_KEY -u CHATGPT_EMAIL -u CHATGPT_PASSWORD -u CHATGPT_TOTP_KEY -u CHATGPT_2FA_KEY -u GH_TOKEN -u GITHUB_TOKEN -u OPENAI_2FA_KEY -u OPENAI_PASSWORD -u OPENAI_USERNAME -u OPENCODE_AUTH_CONTENT -u OPENCODE_GO_API_KEY -u OPENCODE_GO_2_API_KEY -u OPENROUTER_API_KEY timeout "$operation_timeout" "$@"
    }
    if ! run_with_deadline "$command_timeout" playwright-cli "-s=$session" snapshot > "$snapshot_tmp" 2> /dev/null; then
      printf 'Playwright CLI could not capture the login page\n' >&2
      return 1
    fi
    if ! mv -f -- "$snapshot_tmp" "$snapshot_file"; then
      return 1
    fi
    snapshot_tmp=$(mktemp "$snapshot_file.XXXXXX")
    chmod 600 "$snapshot_tmp"
    : > "$command_file"
    local remaining=$((deadline - SECONDS))
    ((remaining > 0)) || break
    local run_timeout="$agent_timeout"
    ((run_timeout > remaining)) && run_timeout=$remaining
    if ! run_agent_with_deadline "$run_timeout" opencode run --auto --model "$agent_model" --title chatgpt-auth-recovery "$recovery_prompt" > /dev/null 2>&1; then
      ((SECONDS < deadline)) && sleep 1
      continue
    fi
    if [[ -s "$command_file" ]]; then
      local batch_file
      batch_file=$(mktemp "$command_file.batch.XXXXXX")
      chmod 600 "$batch_file"
      mv -f -- "$command_file" "$batch_file"
      : > "$command_file"
      chmod 600 "$command_file"
      command_count=0
      while IFS= read -r command || [[ -n "$command" ]]; do
        command_count=$((command_count + 1))
        ((command_count <= 32)) || return 1
        case "$command" in
          snapshot)
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" snapshot > "$snapshot_tmp" 2> /dev/null || return 1
            mv -f -- "$snapshot_tmp" "$snapshot_file" || return 1
            snapshot_tmp=$(mktemp "$snapshot_file.XXXXXX")
            chmod 600 "$snapshot_tmp"
            ;;
          click\ e[0-9]*)
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" click "${command#click }" > /dev/null 2>&1 || return 1
            ;;
          hover\ e[0-9]*)
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" hover "${command#hover }" > /dev/null 2>&1 || return 1
            ;;
          press\ [A-Za-z]*)
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" press "${command#press }" > /dev/null 2>&1 || return 1
            ;;
          check\ e[0-9]*)
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" check "${command#check }" > /dev/null 2>&1 || return 1
            ;;
          uncheck\ e[0-9]*)
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" uncheck "${command#uncheck }" > /dev/null 2>&1 || return 1
            ;;
          fill_secret\ e[0-9]*\ email | fill_secret\ e[0-9]*\ password | type_secret\ e[0-9]*\ email | type_secret\ e[0-9]*\ password)
            secret_name="${command##* }"
            secret_value="${CHATGPT_EMAIL:-${OPENAI_USERNAME:-}}"
            [[ "$secret_name" == password ]] && secret_value="${CHATGPT_PASSWORD:-${OPENAI_PASSWORD:-}}"
            [[ -n "$secret_value" ]] || return 1
            cli_operation="fill"
            [[ "$command" == type_secret\ * ]] && cli_operation="type"
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" "$cli_operation" "${command#* }" "$secret_value" > /dev/null 2>&1 || return 1
            ;;
          otp\ e[0-9]*)
            otp_code=$(CHATGPT_TOTP_KEY="${CHATGPT_TOTP_KEY:-${CHATGPT_2FA_KEY:-${OPENAI_2FA_KEY:-}}}" uv run python -c 'import os, pyotp; print(pyotp.TOTP(os.environ["CHATGPT_TOTP_KEY"]).now())') || return 1
            [[ "$otp_code" =~ ^[0-9]{6}$ ]] || return 1
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" fill "${command#otp }" "$otp_code" > /dev/null 2>&1 || return 1
            ;;
          goto\ https://auth.openai.com/* | goto\ https://chatgpt.com/*)
            [[ "${command#goto }" != *\?* && "${command#goto }" != *\#* ]] || return 1
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" goto "${command#goto }" > /dev/null 2>&1 || return 1
            ;;
          status)
            ;;
          *)
            rm -f -- "$batch_file"
            printf 'Playwright handoff rejected an invalid command\n' >&2
            return 1
            ;;
        esac
      done < "$batch_file"
      rm -f -- "$batch_file"
    fi
    status_json=$(run_with_deadline "$command_timeout" uv run scripts/chatgpt-login.py status) || return 1
    printf '%s\n' "$status_json" | jq -e 'type == "object" and (.error == null) and (.step | type == "string")' > /dev/null || return 1
    if [[ "$(jq -r '.code_ready // false' <<< "$status_json")" == true ]]; then
      handoff_ready=true
      break
    fi
    remaining=$((deadline - SECONDS))
    ((remaining > 0)) || break
    sleep $((remaining < 1 ? remaining : 1))
  done
  rm -f -- "$command_file"
  if [[ "$handoff_ready" != true ]]; then
    printf 'Playwright handoff timed out\n' >&2
    return 1
  fi
  if ! run_with_deadline "$handoff_timeout" playwright-cli "-s=$session" detach > /dev/null 2>&1; then
    printf 'Playwright CLI could not detach from the login browser\n' >&2
    return 1
  fi
  attached=false
  if ! mv -f -- "$handoff_tmp" "$handoff_file"; then
    return 1
  fi
  handoff_tmp=''
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
