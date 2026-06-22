# 0028 — OCC retries use jittered backoff; the 503 advertises a bounded Retry-After

- Status: Accepted
- Date: 2026-06-21

## Context

The transaction envelope retries `OccConflict` up to `MAX_OCC_RETRIES` (5) and,
on exhaustion, raises `RetryExhausted` → HTTP 503 (ADR-0020). The loop re-entered
the handler **with zero delay** between attempts, and the 503 advertised
`Retry-After: 0` (issue #72).

Two contention pathologies followed. The pick/cancel-vs-reaper paths take row
locks in opposite orders (the ABBA cycle documented in ADR-0019); Postgres breaks
the resulting deadlock (40P01) and the adapter translates it to `OccConflict`
(ADR-0020), which this same loop absorbs. With no spacing, a loser re-runs in
microseconds against the same hot rows and can re-form the deadlock or re-lose the
same CAS, burning all five attempts and returning 503 where a few milliseconds of
spacing would have let it commit. `Retry-After: 0` then invited every contending
client to rejoin on the same beat, sustaining the herd.

Correctness was never at risk — the CAS and conditional guards arbitrate
regardless — but the load harness, which exists precisely to exercise this
contention, would read an inflated 503 rate.

## Decision

**Space OCC retries with full-jitter exponential backoff.** Between attempts the
envelope sleeps `rand() * min(cap, base · 2^attempt)` (`base` 10ms, `cap` 200ms),
injected via a `sleep`/`rand` seam so the unit suite stays deterministic. Full
jitter (uniform in `[0, window]`) both lets the contending writer commit and
de-synchronizes the herd so losers do not retry in lockstep. No pause follows the
final attempt — it is about to raise — and the idempotency-claim (`EXISTS`)
replay branch stays backoff-free, since it does not retry.

**Advertise a bounded, non-zero `Retry-After`.** The `RetryExhausted` 503 now
carries a small jittered value (1–3s) instead of `0`, so a client that does honor
it spreads its retry rather than rejoining immediately.

**Leave the ABBA lock-order inversion in place.** Backoff *mitigates* the
deadlock-driven 503s; it does not resolve the opposite lock orderings at the
source (ADR-0019). Aligning the reaper and pick/cancel lock orders is a larger,
separately-reasoned change; this ADR records that the mitigation, not the
resolution, is the accepted V1 posture.

## Consequences

- Under contention most commands that would have exhausted their budget in
  microseconds now commit after a brief jittered pause, lowering the 503 rate the
  load harness observes without touching any correctness guarantee.
- The 503 status itself is unchanged (still the intended terminal, ADR-0020); only
  the spacing and the `Retry-After` value changed.
- `execute` gains `sleep` and `rand` seams (defaulting to `asyncio.sleep` /
  `random.random`); the 13 call sites are unaffected (keyword-only, defaulted).
- The deadlock surface from ADR-0019 remains; if the harness still shows an
  unacceptable 503 rate, aligning the lock orders is the next lever.
