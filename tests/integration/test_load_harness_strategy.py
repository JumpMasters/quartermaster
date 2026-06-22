"""run_strategy proves the per-strategy result on a contended workload."""

from __future__ import annotations

from loadtest.harness import StrategyReport, run_strategy
from sqlalchemy.ext.asyncio import AsyncEngine

from quartermaster.application.oracle import CheckStatus


async def _run(engine: AsyncEngine, strategy: str) -> StrategyReport:
    return await run_strategy(
        engine,
        strategy=strategy,
        seed=99,
        n_skus=4,
        n_orders=64,
        on_hand=8,
        qty=1,
        concurrency=32,
        dup=1,
    )


async def test_guarded_is_clean(committed_db: AsyncEngine) -> None:
    report = await _run(committed_db, "guarded")
    assert report.oracle.ok
    assert report.oversell == 0
    assert report.metrics.errors == 0


async def test_naive_oversells(committed_db: AsyncEngine) -> None:
    report = await _run(committed_db, "naive")
    assert report.oracle.check("conservation_reserved").status is CheckStatus.FAILED
    assert report.oversell > 0
    assert report.metrics.errors == 0  # the lost update is silent, not an error


async def test_read_cas_is_clean_but_thrashes(committed_db: AsyncEngine) -> None:
    report = await _run(committed_db, "read_cas")
    assert report.oracle.ok
    assert report.oversell == 0
    assert report.metrics.total_retries > 0
