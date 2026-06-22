# 0030 — One replica per worker type; only the idempotency GC partitions with SKIP LOCKED

- Status: Accepted
- Date: 2026-06-21

## Context

The reservation reaper, backorder sweep, and idempotency reaper each select a
candidate batch and then act per-row. The reaper/sweep read their batch in a
short transaction that **closes before** the per-item work, which runs in its own
bounded transaction (ADR-0017); the idempotency GC is a single
`DELETE ... WHERE key IN (SELECT ... LIMIT n)`. None of the candidate selects
took row locks (issue #65).

Running more than one replica of a single worker type therefore does not scale
throughput: every instance reads the *same* oldest-N rows and races on them.
Correctness holds — the per-item CAS makes the loser a defined no-op (ADR-0018,
ADR-0019) — but each loser still burns a full transaction, and for the
idempotency GC the overlapping `DELETE`s take row locks and **block** each other
under READ COMMITTED, so the sweep meant to keep the claim INSERT the fast
serialization point can itself become a lock-wait bottleneck.

The naive fix — `FOR UPDATE SKIP LOCKED` on every candidate select — does not
work uniformly. For the reaper and sweep the lock would release the instant their
read transaction closed, before any item is acted on, so it would partition
nothing while *looking* like it did. It only genuinely partitions where the
select and the act are the same statement.

## Decision

**One replica per worker type is the operational contract.** ADR-0017's scaling
story is to split *different* worker types into separate processes, not to run N
replicas of one type. Make that explicit and rely on it: the reservation reaper,
backorder sweep, and idempotency reaper each run as a single instance. The
per-item CAS remains the correctness backstop, so an accidental second instance
is safe (it wastes transactions, never double-acts), but it is not a supported
scaling mode.

**The reaper/sweep candidate selects stay non-locking, with the reason recorded
in code.** `due_for_expiry` and `backordered_orders` deliberately omit
`FOR UPDATE SKIP LOCKED`: it would be a no-op given the select-then-act
transaction boundary, and a misleading one. Comments at both call sites point
here.

**Only the idempotency GC uses `FOR UPDATE SKIP LOCKED`,** in its
`DELETE ... WHERE key IN (SELECT ... LIMIT n FOR UPDATE SKIP LOCKED)`. Because the
delete is one statement, the lock is held for the delete, so this genuinely
partitions: if the single-instance contract is ever violated, a second GC sweep
degrades gracefully to a disjoint batch instead of blocking on the first. It is
defense-in-depth, not an invitation to run multiple reapers.

Proper multi-instance partitioning for the reaper/sweep (a lease/claim column, or
holding select+act in one transaction) is intentionally **not** built: it
conflicts with the bounded-per-item-transaction model and raises the ABBA
deadlock surface, for a scaling mode the contract does not promise.

## Consequences

- The load harness runs one instance of each worker type; it does not expect
  reaper/sweep throughput to scale with replicas, and asserts no invariant breaks
  if it ever runs more than one (the CAS holds).
- The idempotency GC no longer self-serializes on shared row locks, and survives a
  misconfigured second instance by taking disjoint batches.
- Horizontal scaling of a single worker type, if ever needed, requires the
  deferred lease-column work; this ADR records why it was not done for V1.
