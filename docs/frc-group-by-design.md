# frc-group-by design note

Branch: feature/frc-group-by (SHA c305b1685, tracking origin/feature/frc-group-by)
Read: AGENTS.md — target feature repo is a separate "todo repository" and is NOT locally present.

## Decision

- PATH B (default): group-by handled in consumer/query layer; no schema-level grouping change.
- PATH A (conditional): only if the missing todo repo defines a native group-by schema (unverified — blocker below).

## Assumption note

Feature implementation required for the separate target repo; it was not fetched into this workspace, so no feature code can be authored here safely without the real schema/contracts.

## Blocker

Target repo (todo repo per AGENTS.md) missing locally. Cannot verify PATH A preconditions (group-by key, aggregation rules, schema fields). Implementation deferred until repo is available.

## Ponytail skip

No invented feature code; only design doc + branch record delivered.
