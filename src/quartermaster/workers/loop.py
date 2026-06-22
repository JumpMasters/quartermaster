"""Shared background-worker infrastructure: the result record and the poll loop.

A worker's unit of work is a ``tick`` coroutine; :func:`run_forever` schedules it
on a fixed interval and keeps the loop alive across transient failures (a raised
tick is logged, not fatal). The inter-tick wait is cancellable via ``stop`` so a
SIGTERM during a long interval returns promptly instead of sleeping it out, and
each tick runs under an optional watchdog (``tick_timeout``) so a hung query or
lock wait cannot pin a worker indefinitely (issue #75). ``run_tick`` and ``wait``
are seams so tests drive a deterministic number of iterations without real time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

Tick = Callable[[], Awaitable[object]]


@dataclass(frozen=True)
class ReaperRun:
    """Telemetry for one bounded reaper pass.

    ``conflicts`` counts benign contention (a translated deadlock or lost CAS) that
    rolls back and self-heals on a later tick; ``invariant_violations`` counts the
    genuine integrity breaches the reaper is uniquely positioned to detect (a held
    reservation whose stock/line cannot be unwound). Both are kept apart from
    ``errors`` so a real fault is not buried under routine races (issue #66).
    """

    scanned: int = 0
    acted: int = 0
    reopened: int = 0
    errors: int = 0
    conflicts: int = 0
    invariant_violations: int = 0


async def _run_tick(tick: Tick, tick_timeout: float | None) -> None:
    """Run one ``tick``, optionally under a cancellation watchdog.

    A raised tick is logged, never fatal. When ``tick_timeout`` is set, a tick
    that overruns is cancelled and surfaces as a ``TimeoutError`` here -- logged
    and swallowed so the loop recovers on the next interval rather than wedging.
    """
    try:
        if tick_timeout is None:
            await tick()
        else:
            await asyncio.wait_for(tick(), timeout=tick_timeout)
    except TimeoutError:
        logger.warning(
            "worker tick exceeded its %.0fs watchdog and was cancelled; continuing",
            tick_timeout,
        )
    except Exception:
        logger.exception("worker tick failed; continuing")


async def _wait_interval(interval: float, stop: asyncio.Event | None) -> bool:
    """Wait up to ``interval`` seconds, returning early if ``stop`` is set.

    Returns ``True`` when ``stop`` was observed set (the caller should exit), so a
    shutdown signal is honored within the wait rather than only at the next
    interval boundary. With no ``stop`` event it is a plain sleep.
    """
    if stop is None:
        await asyncio.sleep(interval)
        return False
    try:
        await asyncio.wait_for(stop.wait(), timeout=interval)
    except TimeoutError:
        return False
    return True


async def run_forever(
    tick: Tick,
    *,
    interval: float,
    stop: asyncio.Event | None = None,
    tick_timeout: float | None = None,
    run_tick: Callable[[Tick, float | None], Awaitable[None]] = _run_tick,
    wait: Callable[[float, asyncio.Event | None], Awaitable[bool]] = _wait_interval,
) -> None:
    """Run ``tick`` then wait ``interval``, repeating until ``stop`` is set."""
    while stop is None or not stop.is_set():
        await run_tick(tick, tick_timeout)
        if await wait(interval, stop):
            break
