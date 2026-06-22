"""run_workers wires both reaper loops without touching a database."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import quartermaster.app as app_module
from quartermaster.app import run_workers


async def test_run_workers_schedules_all_loops(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QM_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    intervals: list[float] = []
    timeouts: list[float | None] = []

    async def fake_run_forever(
        tick: Callable[[], Awaitable[object]],
        *,
        interval: float,
        stop: object = None,
        tick_timeout: float | None = None,
    ) -> None:
        intervals.append(interval)
        timeouts.append(tick_timeout)

    monkeypatch.setattr(app_module, "run_forever", fake_run_forever)

    await run_workers()

    assert sorted(intervals) == [30.0, 60.0, 3600.0]
    # Every loop is armed with the per-tick watchdog (issue #75).
    assert timeouts == [120.0, 120.0, 120.0]
