# 0009 — Order cancellation is release-only (pre-pick states)

- Status: Accepted
- Date: 2026-06-17

## Context

Cancelling an order must undo its claim on stock. Before any pick, that claim is
purely a reservation. Once picking begins, stock has physically left the shelf,
so "cancelling" would mean putting physical stock back — a different operation
from releasing a reservation.

## Decision

Cancel is legal only from `created`, `allocated`, and `backordered` — the states
that have yet to take, or still hold, reservations. From those states cancel is a
pure `−reserved` release. From `picking` onward, unwinding an order is a restock
that flows through the Receipt/RMA path (see 0008), never an inline cancel. The
order state machine encodes this by allowing `cancelled` only as a successor of
the pre-pick states.

## Consequences

- The "stock never teleports back onto a shelf" rule (see 0006, 0008) stays
  intact.
- There is no second restock path duplicating putaway.
- Cancelling an already-picked order is a multi-step physical return rather than
  a flag flip, which matches reality.
- Recorded in PR #11; the transition table is exhaustively unit-tested.
