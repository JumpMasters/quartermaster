# 0026 — Workers classify contention vs. faults, and the sweep retries OCC in-tick

- Status: Accepted
- Date: 2026-06-21

## Context

Both polled background workers wrapped each per-item transaction in
`except Exception: logger.exception(...); errors += 1`, and the backorder sweep
called `allocate` directly without any OCC retry. Two problems followed (issue
#66).

**Undifferentiated counting.** Under the very contention the load harness
exercises, normal races land in this broad `except`:

- In the reservation reaper, a `pick`/`cancel` racing the same reservation forms
  the ABBA deadlock cycle (ADR-0019) that Postgres breaks and the engine
  translates to `OccConflict` (ADR-0020).
- In the sweep, `allocate` raises `OccConflict` on a lost order-header CAS and
  `IllegalTransition` when the order was concurrently allocated or cancelled.

Every such benign race emitted a full stack trace and incremented `errors`, the
counter the harness reads to judge a run. Worse, it collapsed the one runtime
integrity breach the reaper is uniquely positioned to detect — a HELD reservation
whose backing stock or order line cannot be unwound (`InvariantViolation`) — into
the same opaque bucket, so a real corruption signal was indistinguishable from
transient contention and could never escalate.

**No in-tick retry in the sweep.** Because the sweep bypasses the idempotency
envelope, it also skipped the envelope's `MAX_OCC_RETRIES` loop. A transient
conflict propagated straight to the broad `except` and the order was left
backordered until the next tick (default 30s) — a whole-interval fulfilment delay
for a conflict a single immediate retry would have cleared.

## Decision

**Classify caught exceptions in both workers into three channels.**

- `OccConflict` (and, in the sweep, `IllegalTransition`) is benign contention. It
  increments a separate `conflicts` counter, logged at INFO, not `errors`. The
  losing transaction has already rolled back; a later tick retries cleanly.
- `InvariantViolation` (reaper only) increments a distinct `invariant_violations`
  counter, logged at ERROR with a stack trace, so a genuine integrity breach is a
  loud, isolatable signal an operator can alert on rather than one buried among
  routine races. (The command-path classification of `InvariantViolation` is
  ADR-0024; this is its worker-path counterpart.)
- Any other exception remains an `errors++` with `logger.exception` — a genuinely
  unexpected fault.

**Give the sweep an in-tick bounded OCC retry.** The sweep wraps its direct
`allocate` in the same `MAX_OCC_RETRIES` loop the envelope uses, each attempt in a
fresh transaction; exhaustion is a `conflict`, not an `error`. It deliberately
does **not** route through `application.envelope.execute` with the
`sweep:{order_id}` key: that would cache the first SUCCEEDED response and replay a
stale result on a later tick instead of re-allocating after a reaper
de-allocation. Bypassing the *idempotency* envelope stays correct because the
order-state CAS and the guarded conditional reserve are the arbiters
(ADR-0016/0017); bypassing it never required also foregoing in-tick retry.

## Consequences

- Under concurrent load the worker `errors` counters and ERROR logs reflect
  genuine faults, while routine races register as `conflicts` — the load harness
  can read both without conflating them.
- A real reservation/stock divergence surfaces on its own `invariant_violations`
  channel, distinct and escalatable, instead of vanishing into the error count.
- A backordered order whose allocation loses a CAS is filled in the same tick
  rather than waiting a whole interval, without risking a cached-replay staleness
  bug.
- `ReaperRun` gains `conflicts` and `invariant_violations`; `SweepRun` gains
  `conflicts`. Exhausted in-tick retries are counted as one conflict, not as
  repeated errors.
