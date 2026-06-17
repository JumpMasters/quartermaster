# 0007 — Inbound and outbound flows are decoupled

- Status: Accepted
- Date: 2026-06-17

## Context

Putaway adds stock to shelves; backordered order lines are waiting for exactly
that stock. The tempting design is to re-allocate backordered lines inline when
putaway lands the stock. But an inline fan-out would hold the hot stock-row lock
while touching many waiting order rows in the same transaction, inviting lock
contention and deadlocks.

## Decision

We decouple inbound from outbound. `putaway` only adds stock and appends a
movement; it never re-allocates inline. A background **backorder fulfilment
sweep** picks up backordered order lines FIFO by order age and runs the standard,
isolated `allocate` — **one order per transaction**. Polling is the V1 trigger;
`LISTEN/NOTIFY` on putaway is an optional later optimisation.

## Consequences

- No cross-order fan-out under a held lock, so no deadlock engine to reason
  about.
- The invariant-guarded conditional reserve (see 0003, 0006) keeps several
  competing backorders correct and fair regardless of which caller allocates, so
  the sweep is safe to run alongside live orders.
- Backorder fulfilment is eventual rather than synchronous — an acceptable and
  arguably more realistic behaviour, paid for with a small polling delay.
