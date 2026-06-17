# 0004 — Idempotency caches successes and hard rejections; business failures roll back

- Status: Accepted
- Date: 2026-06-17

## Context

Every mutation is a command carrying an `Idempotency-Key`, and a duplicated or
retried command must apply its effect exactly once. The key is claimed by an
`INSERT` whose unique index serialises duplicates; a concurrent duplicate blocks
on the index, then finds the conflict and replays the stored response. The open
question is which command outcomes to persist under the key.

A command can end three ways: it succeeds; it is rejected for a hard, stable
reason (for example a key reused with a different command fingerprint); or it
fails for a transient business reason (for example insufficient stock right now).

## Decision

We cache successes and hard validation rejections. A business failure rolls the
whole transaction back and the key is **not** persisted, so an identical retry
may succeed later when conditions change (stock arrives). The effect and the
idempotency record commit in the same transaction, so there is no window where
one lands without the other.

## Consequences

- Successful effects are exactly-once; a retry replays the stored response
  instead of re-applying.
- A transient shortfall is never pinned as a stale "out of stock" answer — the
  same key retried after a restock can succeed.
- Hard rejections are stable and cheap to return on retry.
- A client retrying a business-failed command genuinely re-runs it; this is
  intended, because the command is legitimately re-attemptable.
- The alternative — caching the business rejection too — was rejected because it
  can pin a stale negative answer to a key.
