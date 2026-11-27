set -Eeuo pipefail
output_file=$1
total_minutes=$2
idle_minutes=$3
status_file=$4
agent=$5
model=$6
title=$7
prompt=$8
if [[ ! "$total_minutes" =~ ^[1-9][0-9]*$ || ! "$idle_minutes" =~ ^[1-9][0-9]*$ ]]; then
  printf 'Coordinator timeouts must be positive integers: total=%s idle=%s\n' "$total_minutes" "$idle_minutes" >&2
  exit 2
fi
: > "$output_file"
# Launch opencode in its own process group so kill -- -$pid reaches it (GNU timeout forks a group and would miss it). Output is captured to a file and tailed live while the loop samples file size.
setsid opencode run --agent "$agent" --model "$model" --title "$title" "$prompt" > "$output_file" 2>&1 &
child_pid=$!
# Live-stream the captured output to stdout (Actions log) without coupling to opencode's process group.
tail -n +1 -f "$output_file" &
tail_pid=$!
cleanup_done=0
cleanup() {
  ((cleanup_done)) && return
  cleanup_done=1
  if kill -0 "$child_pid" 2>/dev/null; then
    kill -TERM -- "-$child_pid" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  kill "$tail_pid" 2>/dev/null || true
  wait "$tail_pid" 2>/dev/null || true
}
trap cleanup EXIT
start_time=$(date +%s)
last_growth_time=$start_time
last_bytes=0
last_mtime=$(stat -c %Y "$output_file" 2>/dev/null || printf '0')
total_seconds=$((total_minutes * 60))
idle_seconds=$((idle_minutes * 60))
failure_mode=''
storage_dir="$HOME/.local/share/opencode"
storage_state() {
  [[ -d "$storage_dir" ]] || { printf 'none\n'; return; }
  { grep -rl --binary-files=text -- "$title" "$storage_dir" 2>/dev/null || true; } | while IFS= read -r f; do stat -c '%n %Y %s' "$f" 2>/dev/null || true; done | LC_ALL=C sort | sha256sum | cut -d' ' -f1
}
last_storage=$(storage_state)
while kill -0 "$child_pid" 2>/dev/null; do
  now=$(date +%s)
  bytes=$(stat -c %s "$output_file" 2>/dev/null || printf '0')
  mtime=$(stat -c %Y "$output_file" 2>/dev/null || printf '0')
  storage=$(storage_state)
  if ((bytes > last_bytes)) || ((mtime > last_mtime)) || [[ "$storage" != "$last_storage" ]]; then
    last_bytes=$bytes
    last_mtime=$mtime
    last_storage=$storage
    last_growth_time=$now
  fi
  if ((now - start_time >= total_seconds)); then
    failure_mode=timeout
    printf 'Coordinator total timeout after %ss; model=%s; session=%s; output_bytes=%s\n' "$((now - start_time))" "$model" "$title" "$bytes" >&2
    cleanup
    opencode_status=124
    break
  fi
  if ((now - last_growth_time >= idle_seconds)) && ! pgrep -P "$child_pid" >/dev/null 2>&1; then
    failure_mode=zero-progress
    diagnostic=$(printf '::error::coordinator zero-progress after %sm (grace=%sm, model=%s, session=%s, last_output_bytes=%s)\n' "$(((now - start_time) / 60))" "$idle_minutes" "$model" "$title" "$bytes")
    printf '%s\n' "$diagnostic" > "$RUNNER_TEMP/coordinator-diagnostic"
    cleanup
    opencode_status=124
    break
  fi
  sleep 30
done
if [[ -z "$failure_mode" ]]; then
  set +e
  wait "$child_pid"
  opencode_status=$?
  set -e
fi
kill "$tail_pid" 2>/dev/null || true
wait "$tail_pid" 2>/dev/null || true
printf '%s\n' "$opencode_status" > "$status_file"
printf '%s\n' "$failure_mode" > "$RUNNER_TEMP/coordinator-failure-mode"
if [[ -n "$failure_mode" ]]; then
  exit 124
fi
