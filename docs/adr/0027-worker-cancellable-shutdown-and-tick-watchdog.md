# 0027 — Worker loop has a cancellable interval wait and a per-tick watchdog

- Status: Accepted
- Date: 2026-06-21

## Context

ADR-0017 fixed the polled-worker shape: a bounded `tick`, a generic
`run_forever` driver, and a shared `stop` event set by the SIGTERM/SIGINT
handlers in `run_workers`. The original driver was `await tick()` followed by
`await sleep(interval)`, checking `stop` only at the top of the loop. Two
operability gaps followed (issue #75).

**Shutdown is honored only at interval boundaries.** Setting `stop` does not
interrupt an in-flight `await asyncio.sleep(interval)`; the loop finishes the
whole sleep before re-checking. The idempotency reaper's interval defaults to
3600s, so a SIGTERM arriving just after a tick could wait up to an hour before
`run_workers` returns and `engine.dispose()` runs — long past any orchestrator
grace period, so the process is SIGKILLed and disposal is skipped. This defeats
ADR-0017's intent that shutdown flows through the stop event.

**No per-tick watchdog.** A tick that hangs on a slow query or a row-lock wait
pins that worker indefinitely with no cancellation and no chance to observe
`stop`, silently wedging background reconciliation (de-allocation, backorder
fulfilment, idempotency GC).

Both went unnoticed because the unit tests injected deterministic `sleep`/`stop`
seams that sidestepped real `asyncio.sleep` blocking.

## Decision

**Make the inter-tick wait cancellable.** When a `stop` event is present the
driver waits with `asyncio.wait_for(stop.wait(), timeout=interval)`: the interval
elapsing raises `TimeoutError` (a normal tick boundary), and `stop` firing
mid-wait completes immediately and exits the loop. Shutdown latency drops from up
to one full interval to ~0, independent of how long that interval is.

**Add a per-tick watchdog.** A tick runs under
`asyncio.wait_for(tick(), timeout=tick_timeout)`; a tick that overruns is
cancelled and surfaces as a `TimeoutError`, which is logged at WARNING and
swallowed so the loop recovers on the next interval rather than wedging. The
timeout is `Settings.worker_tick_timeout_s` (default 120s) — a generous backstop
above any healthy bounded-batch pass, not a tuning knob. Cancelling mid-tick is
safe because every per-item unit of work is its own bounded transaction
(ADR-0017): an interrupted tick leaves no partial state, only an unfinished
batch a later tick repeats.

The driver's test seams change shape accordingly: `run_forever` now injects
`run_tick` and `wait` (replacing the old `sleep` seam), so the orchestration is
still driven through an exact iteration count with no real delay, while the real
`_run_tick` and `_wait_interval` helpers are unit-tested directly for the
watchdog-cancels-a-hung-tick and stop-interrupts-the-wait behaviors.

## Consequences

- A SIGTERM/SIGINT returns the worker process promptly regardless of interval, so
  `engine.dispose()` runs within the grace period and connections close cleanly.
- A single hung query can no longer stall a worker forever; the watchdog cancels
  it and the loop continues, bounding the blast radius of a stuck transaction
  (and complementing the server-side `statement_timeout`/`lock_timeout` work
  tracked separately in #37).
- `run_forever` gains `tick_timeout`; `Settings` gains `worker_tick_timeout_s`.
  The seam surface ADR-0017 described (`sleep`, `stop`) becomes (`run_tick`,
  `wait`, `stop`); the bounded-pass model and single-composition-root entrypoint
  are unchanged. This refines ADR-0017's execution model rather than superseding
  it.
