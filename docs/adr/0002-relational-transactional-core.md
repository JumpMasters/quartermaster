# 0002 — Relational, transactional core over event sourcing

- Status: Accepted
- Date: 2026-06-17

## Context

Quartermaster's purpose is to keep inventory invariants correct under concurrent
load. Two broad architectures could carry that goal. An event-sourced design
would treat each aggregate as a stream of events, rebuild state by replay, and
serialise concurrent writes per aggregate. A relational, transactional design
would keep current state in tables and let the database enforce invariants under
contention, within ACID transactions.

The hard part here is concurrent correctness on shared stock — many orders
competing for the same `(SKU, location)` row. That is a contention problem on a
shared quantity, not an aggregate-history problem.

## Decision

We build a relational, transactional core. Stock and documents are rows; the
invariants are enforced by Postgres — `CHECK` constraints, invariant-guarded
conditional writes, and state/version compare-and-swap — inside ordinary
transactions. We do not event-source the domain.

The `movement` table is an append-only ledger used for audit and an offline
conservation check; it is not the source of truth and is never replayed to
reconstruct state.

## Consequences

- Correctness under load becomes a database guarantee (row locks, constraints)
  rather than something the application must serialise by hand.
- The mental model stays small: no event store, no projections, no replay
  infrastructure.
- A durable audit trail is still available through the movement ledger, without
  making events the system of record.
- Some domain logic lives in SQL guards rather than in pure in-memory
  aggregates; this is an accepted trade for enforcement that holds under real
  concurrency.
