# 0005 — READ COMMITTED with conditional writes on the hot paths

- Status: Accepted
- Date: 2026-06-17

## Context

Allocating an order line of N units may span several locations within one
transaction, performing several conditional reserves. We need isolation strong
enough that concurrent allocations cannot oversell, without paying for more than
the workload requires. `SERIALIZABLE` would prevent anomalies but introduces
serialization failures and retries on the busiest rows.

## Decision

We run the hot command paths at `READ COMMITTED`. Correctness rests on every
stock mutation being an invariant-guarded conditional write (see 0003): each
`UPDATE` re-checks its guard against the locked, committed row, so there is no
read-modify-write gap to protect against. `SERIALIZABLE` is therefore
unnecessary on these paths.

## Consequences

- Lower isolation cost and no serialization-failure retries introduced by the
  isolation level itself.
- The guarantee depends on discipline: any future code path that reads a stock
  value and then writes based on it — rather than guarding inside the `WHERE` —
  would reintroduce the gap and need its own protection.
- Multi-row allocation stays atomic within the single transaction: reserve what
  is available across locations, backorder the remainder, all or nothing.
