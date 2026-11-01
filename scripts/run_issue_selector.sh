#!/usr/bin/env bash
set -Eeuo pipefail

prompt_file=$1
selection_file=$2
candidate_file=$3
fatal_file=$4
timeout_minutes=$5
shift 5
models=("$@")

if ((${#models[@]} == 0)); then
  printf '%s\n' 'No issue-selection model is available; not dispatching.' >&2
  exit 1
fi

for index in "${!models[@]}"; do
  model=${models[$index]}
  : > "$selection_file"
  printf '%s\n' "$model" > "${selection_file}.failed-model"
  rm -f "$fatal_file"
  set +e
  stop_token=$(openssl rand -hex 32)
  : "${stop_token:?stop-commands fence token generation failed}"
  printf '::stop-commands::%s\n' "$stop_token"
  timeout "${timeout_minutes}m" opencode run --agent issue-selector --model "$model" --title issue-selector "$(<"$prompt_file")" | tee "$selection_file"
  statuses=("${PIPESTATUS[@]}")
  printf '::%s::\n' "$stop_token"
  set -e
  printf 'Model selection statuses (%s): opencode=%s tee=%s\n' "$model" "${statuses[0]}" "${statuses[1]}"
  (( statuses[1] == 0 )) || exit "${statuses[1]}"
  if (( statuses[0] != 0 )); then
    (( statuses[0] == 124 )) && printf '::error::issue selection exceeded the %sm limit for %s\n' "$timeout_minutes" "$model"
    if (( index + 1 < ${#models[@]} )); then
      continue
    fi
    printf 'Selection failed (model=%s opencode=%s); not dispatching.\n' "$model" "${statuses[0]}" >&2
    exit 1
  fi
  if ! grep -q '[^[:space:]]' "$selection_file"; then
    printf '%s\n' 'Selection produced no output; not dispatching.' >&2
    exit 1
  fi
  selected=$(grep -E '^SELECTED_ISSUE:[[:space:]]*[0-9]+[[:space:]]*$' "$selection_file" | tail -1 | grep -oE '[0-9]+' || true)
  if [[ -z "$selected" ]] || ! grep -qxF "$selected" "$candidate_file"; then
    printf '::error::missing or invalid SELECTED_ISSUE; not dispatching\n'
    exit 1
  fi
  printf '%s\n' "$model" > "${selection_file}.model"
  exit 0
done
