# 0032 — The load harness swaps exactly one primitive; `naive` is the falsification control

- Status: Accepted
- Date: 2026-06-22

## Context

ADR-0011 records that conservation is verified by an offline oracle, never on the
command path. ADR-0023 records how that oracle works: it reconstructs on-hand and
reserved balances per `(sku, location)` cell from the `movement` ledger and
compares them against the live `stock` rows. ADR-0023 also records that
`exactly_once` is reported `NOT_CHECKED` by the oracle, because a lockstep
double-apply would balance both the live tables and the ledger reconstruction
equally, leaving conservation intact.

The load harness is a correctness-under-load probe, not a benchmark. Its purpose
is to demonstrate that the production stock-decrement primitive holds the inventory
invariants under real concurrent traffic, and to give that demonstration a
*falsification control* — a known-bad path through the same harness that confirms
the oracle can detect the violation it is meant to detect. Without such a control,
a green oracle result would prove only that no discrepancy was measured, not that
the oracle would have caught one.

## Decision

The harness varies exactly **one primitive** across three strategies, holding the
handler, the transaction envelope, the movement ledger append, and the oracle
identical:

- **`naive`** — a read-modify-write that writes an absolute value with no
  `WHERE` guard. Each write lands within the storage `CHECK` bounds
  (`0 ≤ qty_reserved ≤ qty_on_hand`), so the stock layer accepts it, and the
  lost update is invisible to the storage layer. It is caught only by the oracle's
  `conservation_reserved` check, which reconstructs the expected reserved total
  from the `RESERVE` and `RELEASE` movements and finds it greater than the
  discarded-write-deflated live value. This run is the **falsification control**:
  its oracle `FAIL` is the proof that the oracle would have caught a violation in
  the other two runs.
- **`read_cas`** — a read then a compare-and-swap on the observed
  `(qty_on_hand, qty_reserved)` values. Correct: the update is rejected
  if either value changed since the read, the envelope retries on `OccConflict`,
  and the oracle reports `OK`. Thrashes under contention: retry counts are
  elevated and throughput is lower than the guarded strategy.
- **`guarded`** — the production `_RESERVE_UP_TO` conditional-write primitive.
  The `WHERE` clause is `qty_on_hand - qty_reserved >= want`, so the write
  succeeds exactly when the invariant permits it and fails otherwise — no
  read-modify-write gap, no retry storm, and the oracle reports `OK`.

The strategies are injected behind the `UnitOfWork.stock_repo` seam
(`PostgresUnitOfWork.__init__` accepts an optional `stock_repo_factory`),
so no production code is conditional on harness state outside of `app.py`'s
normal composition root.

**Exactly-once is asserted directly** in a separate harness function
(`assert_exactly_once`): one idempotency key is fired `K` times concurrently and
the balance delta, movement-row count, and reservation-row count are all asserted
to equal exactly one. This is the out-of-band assertion the oracle module
prescribes (ADR-0023, amendment), and it is part of the CI sweep.

## Consequences

- One production DI seam (`stock_repo_factory` on `PostgresUnitOfWork`) is the
  only change to non-harness code. It is not exposed at the API or worker layers.
- The `loadtest` package is held to `mypy --strict` and `ruff` but is explicitly
  **not** coverage-gated: its purpose is to drive the system, not to be covered by
  the system's unit tests.
- CI runs a **scaled-down, deterministic sweep** (`seed=2026`, 4 SKUs, 64 orders,
  8 on-hand per cell, concurrency=32) that asserts the three headline invariants —
  `naive` oversell > 0 and oracle `FAIL`; `read_cas` oracle `OK` with retries > 0;
  `guarded` oracle `OK` with zero retries and zero errors.
- The **full on-demand sweep** is run with `python -m loadtest` (see the README
  for an example invocation and illustrative figures).
- `naive` is permanently part of the sweep and must never be removed or its
  oracle-FAIL assertion weakened: removing it would make the two green results
  unfalsifiable. This is the falsification-control principle that gives each
  `OK` its meaning.
