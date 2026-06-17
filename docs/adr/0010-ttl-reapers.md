# 0010 — Time-to-live and background reapers for reservations and idempotency keys

- Status: Accepted
- Date: 2026-06-17

## Context

Two kinds of row accumulate without bound and need a retention policy.
Reservations hold stock for orders that may be abandoned; without expiry, that
stock stays claimed forever. Idempotency keys accumulate on every command;
without bounding, the table grows and the unique-index `INSERT` that serialises
duplicates becomes a degrading bottleneck.

## Decision

- **Reservations** carry a **15-minute, purely time-based TTL** (`expires_at`). A
  background reaper releases `held` reservations past their expiry: state →
  `expired`, `qty_reserved` lowered, a movement appended. Early abandonment is
  the upstream client's job via an explicit `cancel`, not the engine's to infer.
- **Idempotency keys** carry a **24-hour TTL**. A background reaper batch-deletes
  keys past retention; a retry after that window is semantically a new request.

Each reaper is idempotent and guarded, operating in bounded per-item
transactions rather than one large fan-out.

## Consequences

- Stock freed by an expired reservation returns to `available` for the backorder
  sweep (see 0007).
- The idempotency table stays bounded, so the unique-index `INSERT` remains the
  fast serialization point rather than a growing cost.
- Keeping expiry purely time-based bounds the engine's responsibilities; it does
  not try to detect abandonment.
- A client retrying with the same key after 24 hours is treated as a new
  request, which is acceptable for that retention window.
