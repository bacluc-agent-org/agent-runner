remaining_enrichment_seconds() {
  local remaining=$((enrichment_deadline - SECONDS))
  (( remaining > 0 )) && printf '%s\n' "$remaining" || return 1
}

bounded_enrichment_call() {
  local label="$1"
  shift
  local remaining status started
  if ! remaining="$(remaining_enrichment_seconds)"; then
    printf '::warning::enrichment budget exhausted before %s\n' "$label" >&2
    enrichment_degraded=1
    return 125
  fi
  started="$SECONDS"
  printf 'Enrichment %s started (budget remaining: %ss)\n' "$label" "$remaining" >&2
  set +e
  timeout --signal=TERM --kill-after=2s "${remaining}s" "$@"
  status=$?
  set -e
  printf 'Enrichment %s completed (status: %s, elapsed: %ss)\n' "$label" "$status" "$(( SECONDS - started ))" >&2
  if (( status == 124 || status == 137 )); then
    printf '::warning::enrichment timeout during %s (forced termination after grace period)\n' "$label" >&2
    enrichment_degraded=1
  elif (( status != 0 )); then
    printf '::warning::enrichment lookup failed during %s (status: %s)\n' "$label" "$status" >&2
    enrichment_degraded=1
  fi
  return "$status"
}
