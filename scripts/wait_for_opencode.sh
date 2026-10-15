#!/usr/bin/env bash
# ponytail: fixed 15 attempts; increase if CLI warm-up needs more time
set -Eeuo pipefail

attempts="${1:-15}"
for i in $(seq 1 "$attempts"); do
  if opencode models > /dev/null 2>&1; then
    echo "OpenCode CLI ready after $i attempt(s)"
    exit 0
  fi
  sleep 2
done
echo "OpenCode CLI did not become ready after $attempts attempts" >&2
opencode models > /dev/null 2>&1
