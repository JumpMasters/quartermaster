"""Unit tests for the generic polled-worker loop driver."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from quartermaster.workers.loop import ReaperRun, _run_tick, _wait_interval, run_forever


def test_reaper_run_defaults() -> None:
    assert ReaperRun() == ReaperRun(scanned=0, acted=0, reopened=0, errors=0)
    assert ReaperRun(scanned=3, acted=2, errors=1).acted == 2
    assert ReaperRun(reopened=2).reopened == 2


# --- _wait_interval: the cancellable inter-tick wait -------------------------


async def test_wait_interval_without_stop_sleeps_and_returns_false() -> None:
    assert await _wait_interval(0, None) is False  # no stop event -> plain sleep


async def test_wait_interval_returns_false_when_the_interval_elapses() -> None:
    stop = asyncio.Event()  # never set
    assert await _wait_interval(0.001, stop) is False


async def test_wait_interval_returns_true_when_stop_already_set() -> None:
    stop = asyncio.Event()
    stop.set()
    # A long interval must not be slept through when stop is already set.
    assert await asyncio.wait_for(_wait_interval(3600, stop), timeout=5) is True


async def test_wait_interval_is_interrupted_when_stop_fires_mid_wait() -> None:
    # The core shutdown fix: a worker parked in a long interval wait must return
    # promptly when stop is set, not after the full interval (issue #75).
    stop = asyncio.Event()
    waiting = asyncio.create_task(_wait_interval(3600, stop))
    await asyncio.sleep(0)  # let the task enter the wait
    stop.set()
    assert await asyncio.wait_for(waiting, timeout=5) is True


# --- _run_tick: the per-tick watchdog ----------------------------------------


async def test_run_tick_runs_the_tick_when_no_timeout() -> None:
    ran = []

    async def tick() -> None:
        ran.append(1)

    await _run_tick(tick, None)
    assert ran == [1]


async def test_run_tick_swallows_a_throwing_tick() -> None:
    async def tick() -> None:
        raise RuntimeError("boom")

    await _run_tick(tick, None)  # must not raise


async def test_run_tick_watchdog_cancels_a_hung_tick() -> None:
    # A tick that hangs on a slow query or lock wait must be cancelled by the
    # watchdog rather than pinning the worker indefinitely (issue #75).
    cancelled = False

    async def tick() -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    await asyncio.wait_for(_run_tick(tick, 0.02), timeout=5)  # swallows the TimeoutError
    assert cancelled


# --- run_forever orchestration (deterministic, seam-injected) ----------------


async def _passthrough_tick(tick: Callable[[], Awaitable[object]], _timeout: float | None) -> None:
    await tick()


async def _wait_on_stop(_interval: float, stop: asyncio.Event | None) -> bool:
    return stop is not None and stop.is_set()


async def test_run_forever_runs_until_stop() -> None:
    stop = asyncio.Event()
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        ticks += 1
        if ticks >= 3:
            stop.set()

    await run_forever(tick, interval=99, stop=stop, run_tick=_passthrough_tick, wait=_wait_on_stop)
    assert ticks == 3


async def test_run_forever_passes_tick_timeout_through() -> None:
    stop = asyncio.Event()
    seen: list[float | None] = []

    async def tick() -> None:
        stop.set()

    async def recording_run_tick(
        t: Callable[[], Awaitable[object]], tick_timeout: float | None
    ) -> None:
        seen.append(tick_timeout)
        await t()

    await run_forever(
        tick,
        interval=1,
        stop=stop,
        tick_timeout=12.5,
        run_tick=recording_run_tick,
        wait=_wait_on_stop,
    )
    assert seen == [12.5]


async def test_run_forever_without_stop_loops_until_wait_raises() -> None:
    ticks = 0

    class _Halt(Exception):
        pass

    async def tick() -> None:
        nonlocal ticks
        ticks += 1

    async def wait(_interval: float, _stop: asyncio.Event | None) -> bool:
        if ticks >= 2:
            raise _Halt
        return False

    with pytest.raises(_Halt):
        await run_forever(
            tick, interval=0, run_tick=_passthrough_tick, wait=wait
        )  # stop=None branch
    assert ticks == 2
