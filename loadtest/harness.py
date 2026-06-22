"""Harness orchestration: drive one strategy, then audit with the oracle.

Each strategy runs against a freshly truncated + reseeded store, then the offline
invariant oracle (REPEATABLE READ snapshot) audits the quiesced result. ``oversell``
is the total magnitude of oracle discrepancies — units of stock the ledger and the
live tables disagree on (design spec §8).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from loadtest.metrics import StrategyMetrics, summarize
from loadtest.runner import drive
from loadtest.strategies import STRATEGIES
from loadtest.workload import allocate_thunk, seed_comparative, truncate_all
from quartermaster.adapters.postgres.unit_of_work import postgres_read_uow_factory
from quartermaster.application.oracle import OracleReport, run_oracle
from quartermaster.domain.ids import IdempotencyKey


@dataclass(frozen=True)
class StrategyReport:
    """One strategy's metrics, its oracle audit, and the oversell magnitude."""

    metrics: StrategyMetrics
    oracle: OracleReport
    oversell: int


def violation_magnitude(report: OracleReport) -> int:
    """Sigma |expected - actual| across every discrepancy in every check."""
    return sum(abs(d.expected - d.actual) for c in report.checks for d in c.discrepancies)


async def run_strategy(
    engine: AsyncEngine,
    *,
    strategy: str,
    seed: int,
    n_skus: int,
    n_orders: int,
    on_hand: int,
    qty: int,
    concurrency: int,
    dup: int,
) -> StrategyReport:
    """Truncate, seed, drive ``strategy`` under contention, then audit."""
    await truncate_all(engine)
    rng = random.Random(seed)
    seeded = await seed_comparative(
        engine,
        n_skus=n_skus,
        n_orders=n_orders,
        on_hand_per_cell=on_hand,
        qty_per_order=qty,
        rng=rng,
    )
    uow_factory = STRATEGIES[strategy](engine)
    # dup duplicates share the order's key, so the idempotency layer dedups them:
    # duplicate injection that exercises exactly-once inside the storm (dup=1: none).
    thunks = [
        allocate_thunk(uow_factory, oid, IdempotencyKey(f"{strategy}-{i}"))
        for i, oid in enumerate(seeded.order_ids)
        for _ in range(dup)
    ]
    samples, wall = await drive(thunks, concurrency=concurrency, rand=rng.random)
    metrics = summarize(strategy, samples, wall)
    oracle = await run_oracle(postgres_read_uow_factory(engine))
    return StrategyReport(metrics=metrics, oracle=oracle, oversell=violation_magnitude(oracle))


async def comparative_sweep(
    engine: AsyncEngine,
    *,
    seed: int,
    n_skus: int,
    n_orders: int,
    on_hand: int,
    qty: int,
    concurrency: int,
    dup: int,
) -> list[StrategyReport]:
    """Run all three strategies on the identical workload, in narrative order."""
    return [
        await run_strategy(
            engine,
            strategy=name,
            seed=seed,
            n_skus=n_skus,
            n_orders=n_orders,
            on_hand=on_hand,
            qty=qty,
            concurrency=concurrency,
            dup=dup,
        )
        for name in ("naive", "read_cas", "guarded")
    ]
