#!/usr/bin/env bash
set -Eeuo pipefail

backend="$(command -v gh)" || {
  printf '%s\n' 'gh cli wrapper: real gh backend not found' >&2
  exit 1
}
guard_dir="${RUNNER_TEMP:?}/gh-cli-wrapper"
backend="$(realpath "$backend")"
if [[ ! -x "$backend" || "$backend" == "$(realpath -m "$guard_dir/gh")" ]]; then
  printf '%s\n' 'gh cli wrapper: missing or recursive backend' >&2
  exit 1
fi
mkdir -p "$guard_dir"
cp "$(dirname "${BASH_SOURCE[0]}")/gh_cli_wrapper.py" "$guard_dir/gh"
chmod 755 "$guard_dir/gh"
printf '%s\n' "$backend" > "$guard_dir/real-gh"
cp "$(dirname "${BASH_SOURCE[0]}")/http_guard.py" "$guard_dir/sitecustomize.py"
printf 'PYTHONPATH=%s:%s\n' "$guard_dir" "${PYTHONPATH:-}" >> "${GITHUB_ENV:?}"
printf '%s\n' "$guard_dir" >> "${GITHUB_PATH:?}"
