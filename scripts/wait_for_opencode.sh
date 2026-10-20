#!/usr/bin/env bash
# ponytail: fixed 30 attempts; increase if CLI warm-up needs more time
# ponytail: worst case 30 * (probe timeout 30s + sleep 2s) = ~16 min; covered by each job's timeout-minutes
# The probes fail fast (~3s) while the service installs its location plugins
# (5 npm/git packages from the shared config) on a fresh runner, so extra
# attempts are cheap; 15 were not enough in runs 39325702769 and 39491497101.
set -Eeuo pipefail

attempts="${1:-30}"
out="$(mktemp "${RUNNER_TEMP:-/tmp}/wait_for_opencode.XXXXXX")"

for i in $(seq 1 "$attempts"); do
  if timeout 30 opencode models > "$out" 2>&1; then
    echo "OpenCode CLI ready after $i attempt(s)"
    exit 0
  fi
  sleep 2
done
echo "OpenCode CLI did not become ready after $attempts attempts; last probe output:" >&2
cat "$out"
exit 1
