"""The PR gate: a scaled-down deterministic sweep proving the marquee result.

guarded → oracle green & 0 oversell; naive → oracle red & oversell > 0 (the
falsification control proving the harness can see oversell); read-CAS → green but
retries > 0; one key fired K times → exactly one effect (design spec §10).
"""

from __future__ import annotations

from loadtest.harness import assert_exactly_once, comparative_sweep
from sqlalchemy.ext.asyncio import AsyncEngine

from quartermaster.application.oracle import CheckStatus


async def test_comparative_sweep_proves_bug_fix_proof(committed_db: AsyncEngine) -> None:
    sweep = await comparative_sweep(
        committed_db,
        seed=2026,
        n_skus=4,
        n_orders=64,
        on_hand=8,
        qty=1,
        concurrency=32,
        dup=1,
    )
    reports = {r.metrics.strategy: r for r in sweep}

    naive = reports["naive"]
    assert naive.oracle.check("conservation_reserved").status is CheckStatus.FAILED
    assert naive.oversell > 0
    assert naive.metrics.errors == 0

    cas = reports["read_cas"]
    assert cas.oracle.ok
    assert cas.oversell == 0
    assert cas.metrics.total_retries > 0

    guarded = reports["guarded"]
    assert guarded.oracle.ok
    assert guarded.oversell == 0
    assert guarded.metrics.errors == 0


async def test_exactly_once_under_load(committed_db: AsyncEngine) -> None:
    result = await assert_exactly_once(committed_db, k=16, qty=5)
    assert (result.reserved, result.movement_rows, result.reservation_rows) == (5, 1, 1)
