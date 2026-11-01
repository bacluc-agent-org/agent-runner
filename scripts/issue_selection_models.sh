#!/usr/bin/env bash
set -Eeuo pipefail

available_models_file=$1
deny_list_file=$2

mapfile -t free_models < <(grep -E '^(opencode|openrouter)/[^[:space:]]+(-free|:free)$' "$available_models_file" | grep -Ev -f "$deny_list_file" | awk '!seen[$0]++')
selection_models=()
for candidate in \
  'opencode-go-openai/qwen3.8-flash' \
  'opencode-go-openai-2/qwen3.8-flash' \
  'opencode/big-pickle' \
  "${free_models[@]}"; do
  case "$candidate" in
    opencode-go-openai/qwen3.8-flash)
      grep -Fxq "$candidate" "$available_models_file" && [[ -n "$OPENCODE_GO_API_KEY" ]] || continue
      ;;
    opencode-go-openai-2/qwen3.8-flash)
      grep -Fxq "$candidate" "$available_models_file" && [[ -n "$OPENCODE_GO_2_API_KEY" ]] || continue
      ;;
    opencode/big-pickle)
      grep -Fxq "$candidate" "$available_models_file" || continue
      ;;
    opencode/*-free) ;;
    openrouter/*)
      [[ -n "$OPENROUTER_API_KEY" ]] || continue
      ;;
    *) continue ;;
  esac
  selection_models+=("$candidate")
done
if ((${#selection_models[@]} == 0)); then
  fallback_model="$(grep -Ev -f "$deny_list_file" "$available_models_file" | head -n1 || true)"
  if [[ -z "$fallback_model" ]]; then
    printf '%s\n' 'No issue-selection model is available; not dispatching.' >&2
    exit 1
  fi
  selection_models+=("$fallback_model")
  printf '%s\n' "Using fallback model: $fallback_model" >&2
fi
printf '%s\n' "${selection_models[@]}" | awk '!seen[$0]++' | head -n2
