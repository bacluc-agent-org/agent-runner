#!/usr/bin/env bash
set -Eeuo pipefail

umask 077
auth_dir="$HOME/.local/share/opencode"
auth_file="$auth_dir/auth.json"
rm -f "$auth_file"

if [[ -z "${OPENCODE_AUTH_CONTENT:-}" ]]; then
  exit 0
fi

if ! jq -e '.openai.type == "oauth" and .openai.access != "" and .openai.refresh != "" and (.openai.expires | type == "number")' <<< "$OPENCODE_AUTH_CONTENT" > /dev/null 2>&1; then
  printf '%s\n' '::warning::OPENCODE_AUTH_JSON_V1 is invalid; OpenAI v1 models are disabled for this run.' >&2
  exit 0
fi

install -d -m 700 "$auth_dir"
printf '%s' "$OPENCODE_AUTH_CONTENT" > "$auth_file"
chmod 600 "$auth_file"

if command -v opencode > /dev/null 2>&1 && ! opencode --help > /dev/null 2>&1; then
  printf '%s\n' '::warning::OpenCode rejected the v1 auth file; OpenAI v1 models are disabled for this run.' >&2
  rm -f "$auth_file"
fi
