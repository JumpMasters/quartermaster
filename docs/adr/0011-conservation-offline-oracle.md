# 0011 — Conservation is verified by an offline oracle, not on the command path

- Status: Accepted
- Date: 2026-06-17

## Context

The append-only `movement` ledger makes stock conservation auditable: for each
SKU, the sum of inbound minus outbound movements should equal the current
on-hand total. This catches lost-update and double-apply bugs that the local
`CHECK` constraints cannot. But summing the ledger on every command would put a
growing aggregate on the hot path.

## Decision

Conservation is an **offline / post-run oracle**, never a command-path
operation. The `movement` ledger is append-only and indexed by `(sku_id, ts)`
for tractable per-SKU spot-checks. The conservation sum runs after the fact over
a bounded window — in tests and audits — not continuously. Continuous
large-scale verification would use periodic ledger snapshots (rolling
checkpoints), which are deferred beyond V1.

## Consequences

- The command path stays fast: it appends to the ledger but never sums it.
- The oracle catches whole classes of concurrency bugs (lost updates, double
  application) that local constraints miss.
- Conservation is checked post-run over a bounded window rather than
  continuously; production never sums the ledger, and snapshots are deferred.
- The ledger is an audit and oracle source, not the system of record (see 0002).
