#!/usr/bin/env bash
set -Eeuo pipefail

auth_file="$RUNNER_TEMP/auth.json"
stderr_file="$RUNNER_TEMP/primitives-login.stderr"
login_timeout="${PLAYWRIGHT_LOGIN_TIMEOUT_SECONDS:-600}"
if [[ ! "$login_timeout" =~ ^[1-9][0-9]*$ ]]; then
  printf 'Invalid Playwright login timeout\n' >&2
  exit 1
fi
login_deadline=$((SECONDS + login_timeout))
agent_log="$RUNNER_TEMP/chatgpt-login-recovery.agent.log"
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

redact_snapshot() {
  local file="$1"
  python3 - "$file" << 'PY'
import os
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8", errors="replace") as handle:
    text = handle.read()

for name in (
    "OPENAI_USERNAME",
    "OPENAI_PASSWORD",
    "OPENAI_2FA_KEY",
    "CHATGPT_EMAIL",
    "CHATGPT_PASSWORD",
    "CHATGPT_2FA_KEY",
    "CHATGPT_TOTP_KEY",
):
    value = os.environ.get(name)
    if value and len(value) >= 4:
        text = text.replace(value, "[redacted]")
text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[redacted email]", text, flags=re.I)
text = re.sub(r"(?<!\d)\d{6}(?!\d)", "[redacted code]", text)
text = re.sub(r"\b(?:rt|sk)[.-][A-Z0-9._-]{8,}\b", "[redacted token]", text, flags=re.I)
text = re.sub(r"\beyJ[A-Z0-9_-]{10,}\.[A-Z0-9_-]{10,}(?:\.[A-Z0-9_-]+)?\b", "[redacted token]", text, flags=re.I)
text = re.sub(
    r"([?&](?:code|state|access_token|refresh_token|id_token|token)=)[^&#\s]*",
    r"\1[redacted]",
    text,
    flags=re.I,
)
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text)
PY
}

recovery_attempted=false
recover_blocking_login() {
  local session='chatgpt-auth-recovery'
  local cdp_port="${OPENAI_CDP_PORT:-9222}"
  local snapshot_file="$RUNNER_TEMP/chatgpt-login-recovery.snapshot"
  local handoff_file="$RUNNER_TEMP/chatgpt-login-recovery.handoff"
  local command_file="$RUNNER_TEMP/chatgpt-login-recovery.commands"
  local snapshot_tmp=''
  local status_tmp=''
  local handoff_tmp=''
  local command_tmp=''
  local batch_file=''
  local attached=false
  local handoff_timeout="${PLAYWRIGHT_HANDOFF_TIMEOUT_SECONDS:-300}"
  local command_timeout="${PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS:-20}"
  local agent_timeout="${PLAYWRIGHT_AGENT_TIMEOUT_SECONDS:-120}"
  local agent_model="${PLAYWRIGHT_AGENT_MODEL:-opencode/big-pickle}"
  local opencode_root="$RUNNER_TEMP/chatgpt-login-opencode"
  local opencode_workdir="$opencode_root/workdir"
  local opencode_config="$opencode_root/config"
  local opencode_data="$opencode_root/data"
  local opencode_cache="$opencode_root/cache"
  local opencode_tmp="$opencode_root/tmp"

  if [[ ! "$handoff_timeout" =~ ^[1-9][0-9]*$ || "$handoff_timeout" -gt 300 || ! "$command_timeout" =~ ^[1-9][0-9]*$ || "$command_timeout" -gt 30 || ! "$agent_timeout" =~ ^[1-9][0-9]*$ || "$agent_timeout" -gt 300 ]]; then
    printf 'Invalid Playwright handoff timeout\n' >&2
    return 1
  fi

  local deadline=$((SECONDS + handoff_timeout))
  if ((deadline > login_deadline)); then
    deadline=$login_deadline
  fi

  run_with_deadline() {
    local max_timeout="$1"
    shift
    local remaining=$((deadline - SECONDS))
    ((remaining > 0)) || return 1
    local operation_timeout="$max_timeout"
    ((operation_timeout > remaining)) && operation_timeout=$remaining
    timeout "$operation_timeout" "$@"
  }

  capture_snapshot() {
    run_with_deadline "$command_timeout" playwright-cli "-s=$session" snapshot > "$snapshot_tmp" 2> /dev/null || return 1
    redact_snapshot "$snapshot_tmp" || return 1
    [[ -s "$snapshot_tmp" ]] || return 1
    mv -f -- "$snapshot_tmp" "$snapshot_file" || return 1
    snapshot_tmp=$(mktemp "$snapshot_file.XXXXXX")
    chmod 600 "$snapshot_tmp"
  }

  cleanup_recovery() {
    if [[ "$attached" == true ]]; then
      run_with_deadline "$handoff_timeout" playwright-cli "-s=$session" detach > /dev/null 2>&1 || true
      attached=false
    fi
    rm -f -- "$snapshot_tmp" "$status_tmp" "$handoff_tmp" "$command_tmp" "$batch_file" "$snapshot_file" "$handoff_file" "$command_file" "$agent_log"
    rm -rf -- "$opencode_root"
  }
  trap cleanup_recovery RETURN
  rm -f -- "$snapshot_file" "$handoff_file" "$command_file"
  rm -rf -- "$opencode_root"
  mkdir -m 700 -p "$opencode_root/home" "$opencode_workdir" "$opencode_config" "$opencode_data" "$opencode_cache" "$opencode_tmp"
  cat > "$opencode_config/opencode.json" << 'EOF'
{
  "provider": {
    "opencode": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://opencode.ai/zen/go/v1",
        "apiKey": "{env:OPENCODE_GO_API_KEY}"
      },
      "models": {
        "big-pickle": {
          "name": "big-pickle"
        }
      }
    }
  }
}
EOF
  snapshot_tmp=$(mktemp "$snapshot_file.XXXXXX")
  status_tmp=$(mktemp "$handoff_file.status.XXXXXX")
  handoff_tmp=$(mktemp "$handoff_file.XXXXXX")
  command_tmp=$(mktemp "$command_file.XXXXXX")
  chmod 600 "$snapshot_tmp" "$status_tmp" "$handoff_tmp" "$command_tmp"
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
operations=snapshot|click|hover|check|uncheck|fill|type|fill_secret|type_secret|otp|select|goto|press|reload|go-back|go-forward|status|step
timeout_seconds=$handoff_timeout
command_timeout_seconds=$command_timeout
EOF
  local handoff_ready=false
  local agent_attempt=0
  while ((SECONDS < deadline)); do
    run_agent_with_deadline() {
      local max_timeout="$1"
      shift
      local remaining=$((deadline - SECONDS))
      ((remaining > 0)) || return 1
      local operation_timeout="$max_timeout"
      ((operation_timeout > remaining)) && operation_timeout=$remaining
      (
        cd "$opencode_workdir"
        env -i \
          HOME="$opencode_root/home" XDG_CONFIG_HOME="$opencode_config" XDG_DATA_HOME="$opencode_data" XDG_CACHE_HOME="$opencode_cache" \
          PATH="${PATH:-/usr/local/bin:/usr/bin:/bin}" LANG="${LANG:-C.UTF-8}" \
          TMPDIR="$opencode_tmp" RUNNER_TEMP="${RUNNER_TEMP:-}" FAKE_MODE="${FAKE_MODE:-}" FAKE_COMMAND="${FAKE_COMMAND:-}" \
          OPENCODE_GO_API_KEY="${OPENCODE_GO_API_KEY:-}" \
          OPENCODE_GO_2_API_KEY="${OPENCODE_GO_2_API_KEY:-}" \
          timeout "$operation_timeout" "$@"
      ) 2> "$agent_log"
    }
    if ! capture_snapshot; then
      printf 'Playwright CLI could not capture the login page\n' >&2
      return 1
    fi
    status_json=''
    if ! status_json=$(run_with_deadline "$command_timeout" uv run scripts/chatgpt-login.py status); then
      status_json='{"step":"unknown","url":"","title":"","blocking":"status unavailable","code_ready":false}'
      printf 'Playwright recovery status unavailable; retaining the browser session\n' >&2
    fi
    printf '%s\n' "$status_json" > "$status_tmp"
    redact_snapshot "$status_tmp" || return 1
    sanitized_status=$(jq -ce '{step:((.step // "unknown") | tostring | .[0:100]),url:((.url // "") | tostring | .[0:500]),title:((.title // "") | tostring | .[0:500]),blocking:((.blocking // null) | if . == null then null else tostring | .[0:200] end),code_ready:(.code_ready // false)}' "$status_tmp") || return 1
    snapshot_content=$(< "$snapshot_file")
    [[ -n "$snapshot_content" ]] || return 1
    snapshot_content=${snapshot_content:0:12000}
    remaining=$((deadline - SECONDS))
    recovery_prompt=$(
      cat << EOF
Drive the blocked ChatGPT login through the attached Playwright session.
Current sanitized status (do not infer secrets):
$sanitized_status
Current redacted Playwright snapshot:
$snapshot_content
Write only newline-delimited commands to $command_file, then exit.
Allowed commands are exactly: snapshot, click eN, hover eN, check eN, uncheck eN, fill eN TEXT, type eN TEXT, fill_secret eN email|password, type_secret eN email|password, otp eN, select eN VALUE, goto https://auth.openai.com/PATH, goto https://chatgpt.com/PATH, press KEY, reload, go-back, go-forward, status, step.
OpenAI may change the login flow; adapt the path using the primitives and snapshot instead of assuming a fixed sequence. Use step to invoke the built-in automation primitive when it can make progress.
Use symbolic secret commands for credentials and otp for TOTP; never read, print, or write credential values. Use only refs from the snapshot. URLs must use https on auth.openai.com, chatgpt.com, www.chatgpt.com, or chat.openai.com, with optional path, query, and fragment. TEXT and VALUE may contain up to 200 printable characters. Keep the batch at most 32 commands. The global recovery deadline has $remaining seconds remaining. Do not call playwright-cli directly, modify any other file, or report secrets.
EOF
    )
    printf 'Playwright recovery status: %s\n' "$sanitized_status" >&2
    : > "$command_file"
    remaining=$((deadline - SECONDS))
    ((remaining > 0)) || break
    local run_timeout="$agent_timeout"
    ((run_timeout > remaining)) && run_timeout=$remaining
    ((agent_attempt += 1))
    : > "$agent_log"
    chmod 600 "$agent_log"
    if ! run_agent_with_deadline "$run_timeout" opencode --pure run --auto --print-logs --log-level ERROR --model "$agent_model" --title chatgpt-auth-recovery "$recovery_prompt" > /dev/null; then
      local reason=''
      if [[ -s "$agent_log" ]] && redact_snapshot "$agent_log"; then
        reason=$(tail -c 2000 "$agent_log" || true)
      fi
      if [[ -n "$reason" ]]; then
        stop_token="$(openssl rand -hex 32)"
        : "${stop_token:?stop-commands fence token generation failed}"
        printf '::stop-commands::%s\n' "$stop_token"
        printf 'Playwright recovery agent attempt %s failed; safe diagnostics:\n%s\n' "$agent_attempt" "$reason" >&2
        printf '::%s::\n' "$stop_token"
      else
        printf 'Playwright recovery agent attempt %s failed; retrying within the global deadline\n' "$agent_attempt" >&2
      fi
      local fatal_reason="${reason,,}"
      if [[ "$fatal_reason" =~ ai_apicallerror|ai_retryerror|usage\ limit|api\ key\ has\ expired|authentication|unauthorized|401 ]]; then
        printf 'Playwright recovery agent stopped on a fatal provider error\n' >&2
        return 1
      fi
      ((SECONDS < deadline)) && sleep 1
      continue
    fi
    if [[ -s "$command_file" ]]; then
      batch_file=$(mktemp "$command_file.batch.XXXXXX")
      chmod 600 "$batch_file"
      mv -f -- "$command_file" "$batch_file"
      : > "$command_file"
      chmod 600 "$command_file"
      local command_count=0
      local command=''
      local operation=''
      local ref=''
      local text=''
      local value=''
      local key=''
      local url=''
      local secret_name=''
      local secret_value=''
      local otp_code=''
      while IFS= read -r command || [[ -n "$command" ]]; do
        command="${command#"${command%%[![:space:]]*}"}"
        command="${command%"${command##*[![:space:]]}"}"
        [[ -n "$command" ]] || continue
        ((command_count += 1))
        ((command_count <= 32)) || return 1
        if ((${#command} > 512)) || [[ "$command" == *$'\r'* ]]; then
          printf 'Playwright handoff rejected an invalid command\n' >&2
          return 1
        fi
        if [[ "$command" == snapshot ]]; then
          capture_snapshot || return 1
        elif [[ "$command" =~ ^(click|hover|check|uncheck)\ (e[0-9]+)$ ]]; then
          operation="${BASH_REMATCH[1]}"
          ref="${BASH_REMATCH[2]}"
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" "$operation" "$ref" > /dev/null 2>&1 || return 1
        elif [[ "$command" =~ ^fill\ (e[0-9]+)\ ([[:print:]]{1,200})$ ]]; then
          ref="${BASH_REMATCH[1]}"
          text="${BASH_REMATCH[2]}"
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" fill "$ref" "$text" > /dev/null 2>&1 || return 1
        elif [[ "$command" =~ ^type\ (e[0-9]+)\ ([[:print:]]{1,200})$ ]]; then
          ref="${BASH_REMATCH[1]}"
          text="${BASH_REMATCH[2]}"
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" click "$ref" > /dev/null 2>&1 || return 1
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" type "$text" > /dev/null 2>&1 || return 1
        elif [[ "$command" =~ ^select\ (e[0-9]+)\ ([[:print:]]{1,200})$ ]]; then
          ref="${BASH_REMATCH[1]}"
          value="${BASH_REMATCH[2]}"
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" select "$ref" "$value" > /dev/null 2>&1 || return 1
        elif [[ "$command" =~ ^(fill_secret|type_secret)\ (e[0-9]+)\ (email|password)$ ]]; then
          operation="${BASH_REMATCH[1]}"
          ref="${BASH_REMATCH[2]}"
          secret_name="${BASH_REMATCH[3]}"
          secret_value="${CHATGPT_EMAIL:-${OPENAI_USERNAME:-}}"
          [[ "$secret_name" == password ]] && secret_value="${CHATGPT_PASSWORD:-${OPENAI_PASSWORD:-}}"
          [[ -n "$secret_value" ]] || return 1
          if [[ "$operation" == type_secret ]]; then
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" click "$ref" > /dev/null 2>&1 || return 1
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" type "$secret_value" > /dev/null 2>&1 || return 1
          else
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" fill "$ref" "$secret_value" > /dev/null 2>&1 || return 1
          fi
        elif [[ "$command" =~ ^otp\ (e[0-9]+)$ ]]; then
          ref="${BASH_REMATCH[1]}"
          otp_code=$(CHATGPT_TOTP_KEY="${CHATGPT_TOTP_KEY:-${CHATGPT_2FA_KEY:-${OPENAI_2FA_KEY:-}}}" uv run python -c 'import os, pyotp; print(pyotp.TOTP(os.environ["CHATGPT_TOTP_KEY"]).now())') || return 1
          [[ "$otp_code" =~ ^[0-9]{6}$ ]] || return 1
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" fill "$ref" "$otp_code" > /dev/null 2>&1 || return 1
        elif [[ "$command" =~ ^press\ ([A-Za-z0-9+_-]{1,32})$ ]]; then
          key="${BASH_REMATCH[1]}"
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" press "$key" > /dev/null 2>&1 || return 1
        elif [[ "$command" =~ ^goto\ (https://(auth\.openai\.com|chatgpt\.com|www\.chatgpt\.com|chat\.openai\.com)([/?#][^[:space:]\\]*)?)$ ]]; then
          url="${BASH_REMATCH[1]}"
          run_with_deadline "$command_timeout" playwright-cli "-s=$session" goto "$url" > /dev/null 2>&1 || return 1
        elif [[ "$command" == step ]]; then
          run_with_deadline "$command_timeout" uv run scripts/chatgpt-login.py step > /dev/null 2>&1 || true
        elif [[ "$command" =~ ^(reload|go-back|go-forward|status)$ ]]; then
          operation="${BASH_REMATCH[1]}"
          if [[ "$operation" != status ]]; then
            run_with_deadline "$command_timeout" playwright-cli "-s=$session" "$operation" > /dev/null 2>&1 || return 1
          fi
        else
          printf 'Playwright handoff rejected an invalid command\n' >&2
          return 1
        fi
      done < "$batch_file"
      rm -f -- "$batch_file"
      batch_file=''
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

attempt=0
while ((SECONDS < login_deadline)); do
  ((attempt += 1))
  if ! kill -0 "$start_pid" 2> /dev/null; then
    if ! wait "$start_pid"; then
      printf 'Primitive login startup failed; safe diagnostics:\n' >&2
      stop_token="$(openssl rand -hex 32)"
      : "${stop_token:?stop-commands fence token generation failed}"
      printf '::stop-commands::%s\n' "$stop_token"
      cat "$stderr_file" >&2
      printf '::%s::\n' "$stop_token"
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
    if [[ "$recovery_attempted" == true ]]; then
      printf 'Primitive login remained blocked after Playwright recovery\n' >&2
      exit 1
    fi
    recovery_attempted=true
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
    if ((step_status == 1 || step_status == 2)); then
      if [[ "$recovery_attempted" == true ]]; then
        printf 'Primitive login could not progress after Playwright recovery\n' >&2
        exit 1
      fi
      recovery_attempted=true
      recover_blocking_login || exit 1
      sleep 5
      continue
    fi
    printf 'Primitive login step failed with status %s\n' "$step_status" >&2
    exit "$step_status"
  fi
  printf '%s\n' "$step_json" | jq -e 'type == "object" and (.error == null) and (.step | type == "string")' > /dev/null || {
    printf 'Primitive login step returned invalid status\n' >&2
    exit 1
  }
  sleep 5
done

if kill -0 "$start_pid" 2> /dev/null; then
  printf 'Primitive login timed out after 10 minutes\n' >&2
  exit 1
fi
auth_json=$(cat "$auth_file")
printf '%s\n' "$auth_json" | jq -e '.openai.type == "oauth" and (.openai.access | type == "string" and length > 0) and (.openai.refresh | type == "string" and length > 0) and (.openai.expires | type == "number" and . == floor and . > 0)' > /dev/null || {
  printf 'Primitive login produced invalid authentication JSON\n' >&2
  exit 1
}
printf '::add-mask::%s\n' "$auth_json"
GH_TOKEN="$BACLUC_AGENT_GITHUB_TOKEN" gh secret set OPENCODE_AUTH_JSON --repo "$GITHUB_REPOSITORY" --body "$auth_json"
printf 'Auth refreshed via primitives fallback\n'
