# 0003 — Two flavours of optimistic concurrency control

- Status: Accepted
- Date: 2026-06-17

## Context

Concurrent commands mutate two kinds of state with very different access
patterns. Stock quantity rows are hot and contended: many orders decrement the
same `(SKU, location)` cell. Document state (the order and receipt lifecycles)
changes one row at a time and conflicts are usually duplicate commands. A single
concurrency-control mechanism does not serve both well.

A version-column compare-and-swap on a hot stock row would make concurrent
writers read the same version, lose the swap, and retry — a retry storm with no
correctness benefit on the busiest rows.

## Decision

We use two flavours of optimistic concurrency control.

- **Stock — invariant-guarded conditional writes.** The invariant lives in the
  `WHERE` clause: `UPDATE stock SET … WHERE … AND <guard>`. Zero rows affected
  means the guard failed. No version column, no retry loop.
- **Documents — state/version compare-and-swap with bounded retry.**
  `UPDATE … WHERE id = :id AND state = :expected AND version = :v`. Zero rows
  means someone already transitioned or the read was stale.

## Consequences

- On contended stock rows, every non-violating decrement succeeds (serialised by
  the row lock the `UPDATE` takes) and only genuinely insufficient ones fail,
  definitively, with no retry. This avoids the version-OCC retry storm.
- Document conflicts are detected cleanly. A conflict usually signals a duplicate
  command and is resolved through idempotency (return the current state) rather
  than surfaced as an error; genuine illegal transitions are rejected.
- Two mechanisms must be understood, but each is matched to its access pattern.
- This composes with READ COMMITTED isolation (see 0005): the conditional
  `WHERE` re-checks the locked, committed row, so there is no read-modify-write
  gap.
