"""delete_expired claims disjoint batches under concurrency (issue #65).

The idempotency reaper's purpose is to keep the unique-index claim INSERT the fast
serialization point, so the GC sweep itself must not become a lock-wait
bottleneck. ``DELETE ... WHERE key IN (SELECT ... LIMIT n)`` is a single
statement, so ``FOR UPDATE SKIP LOCKED`` in the subquery genuinely partitions: a
second reaper skips the rows a first is mid-deleting and takes the next batch
instead of blocking on them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from quartermaster.adapters.postgres.tables import idempotency_key
from quartermaster.adapters.postgres.unit_of_work import PostgresUnitOfWork
from quartermaster.domain.ids import IdempotencyKey

_PAST = datetime(2020, 1, 1, tzinfo=UTC)
_CUTOFF = datetime(2030, 1, 1, tzinfo=UTC)  # well after every seeded row


async def _seed_expired(engine: AsyncEngine, n: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            idempotency_key.insert(),
            [
                {
                    "key": f"old-{i}",
                    "command_fingerprint": "fp",
                    "status": "succeeded",
                    "response": {"i": i},
                    "created_at": _PAST,
                }
                for i in range(n)
            ],
        )


async def _remaining(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int(
            (await conn.execute(select(func.count()).select_from(idempotency_key))).scalar_one()
        )


async def test_delete_expired_skips_locked_rows_instead_of_blocking(
    committed_db: AsyncEngine,
) -> None:
    await _seed_expired(committed_db, 4)

    a = PostgresUnitOfWork(committed_db)
    await a.__aenter__()
    try:
        # A deletes (and locks) its batch of 2, uncommitted.
        assert await a.idempotency.delete_expired(_CUTOFF, 2) == 2

        b = PostgresUnitOfWork(committed_db)
        await b.__aenter__()
        try:
            # B must not block on A's locked rows: SKIP LOCKED lets it take the
            # other 2. Without it, B's DELETE would wait on A's row locks and this
            # would time out.
            deleted_b = await asyncio.wait_for(b.idempotency.delete_expired(_CUTOFF, 2), timeout=5)
            assert deleted_b == 2
            await b.commit()
        finally:
            await b.__aexit__(None, None, None)

        await a.commit()
    finally:
        await a.__aexit__(None, None, None)

    assert await _remaining(committed_db) == 0  # disjoint batches removed all 4


async def test_delete_expired_returns_zero_when_a_concurrent_reaper_holds_the_batch(
    committed_db: AsyncEngine,
) -> None:
    # Only one expired row exists; while A holds it, B finds nothing to do and
    # returns 0 immediately rather than blocking.
    await _seed_expired(committed_db, 1)

    a = PostgresUnitOfWork(committed_db)
    await a.__aenter__()
    try:
        assert await a.idempotency.delete_expired(_CUTOFF, 10) == 1

        b = PostgresUnitOfWork(committed_db)
        await b.__aenter__()
        try:
            deleted_b = await asyncio.wait_for(b.idempotency.delete_expired(_CUTOFF, 10), timeout=5)
            assert deleted_b == 0
            await b.commit()
        finally:
            await b.__aexit__(None, None, None)
        await a.commit()
    finally:
        await a.__aexit__(None, None, None)

    # Sanity: the key argument type is exercised for parity with production calls.
    assert IdempotencyKey("old-0")
