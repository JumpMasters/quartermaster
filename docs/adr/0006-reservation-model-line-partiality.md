# 0006 — On-hand / reserved / available reservation model with line-level partiality

- Status: Accepted
- Date: 2026-06-17

## Context

Concurrent orders compete for the same SKU, and the engine must support partial
fulfilment, backorders, and reservations that expire. We need a model for stock
that has been claimed by an order but not yet physically shipped, and a way to
record how far each order or receipt has progressed without exploding the set of
document states.

## Decision

Stock per `(SKU, location)` carries `qty_on_hand` and `qty_reserved`, with
`available = qty_on_hand − qty_reserved`. Allocation **reserves** (raises
`qty_reserved`, guarded by `available ≥ n`) without moving stock. **Picking**
consumes the reservation (lowers both `on_hand` and `reserved`). **Cancel,
expiry, and return** release it (lower `reserved`, or restock `on_hand`).

Partiality is carried at the **line** level — `ordered / allocated / picked /
shipped` on order lines, `expected / received` on receipt lines — so the
document state machines stay small. The domain enforces the monotonic line
invariants (`0 ≤ shipped ≤ picked ≤ allocated ≤ ordered`,
`0 ≤ received ≤ expected`), and `CHECK` constraints enforce
`0 ≤ reserved ≤ on_hand` at the storage layer.

## Consequences

- Concurrent orders compete correctly through the `available ≥ n` guard; partial
  fulfilment and backorders fall out of the line quantities naturally.
- Over-reserve and over-decrement are impossible at the storage layer.
- The lifecycle state set stays small because quantity progress lives on lines,
  not in states.
- Two quantity tiers must be kept consistent (`on_hand` and `reserved`); the
  guarded operations and constraints are what keep them so.
