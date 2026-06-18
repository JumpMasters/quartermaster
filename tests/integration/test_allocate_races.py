# tests/integration/test_allocate_races.py
"""Concurrency races on real Postgres — the slice's centerpiece (design §7)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from quartermaster.adapters.postgres.identifiers import new_movement_id, new_reservation_id
from quartermaster.adapters.postgres.tables import order_line, orders, reservation
from quartermaster.adapters.postgres.unit_of_work import postgres_uow_factory
from quartermaster.application.clock import system_clock
from quartermaster.application.handlers.allocate import run_allocate
from quartermaster.application.results import AllocateResult
from quartermaster.domain.errors import IllegalTransition
from quartermaster.domain.ids import IdempotencyKey, OrderId
from quartermaster.domain.state_machines import OrderState
from tests.integration.seed import assert_invariants, seed_order, seed_sku_locations_stock


def _runner(engine: AsyncEngine) -> Callable[[OrderId, str], Coroutine[Any, Any, AllocateResult]]:
    factory = postgres_uow_factory(engine)

    async def run(order_id: OrderId, key: str) -> AllocateResult:
        return await run_allocate(
            factory,
            order_id,
            IdempotencyKey(key),
            now=system_clock,
            new_reservation_id=new_reservation_id,
            new_movement_id=new_movement_id,
        )

    return run


async def test_n_concurrent_allocates_exactly_m_succeed(committed_db: AsyncEngine) -> None:
    # Centerpiece: 10 distinct orders each want 1 unit; 4 on hand -> exactly 4 allocate.
    m, n = 4, 10
    sku = await seed_sku_locations_stock(committed_db, "S", {"L1": m})
    order_ids = [
        await seed_order(committed_db, state=OrderState.CREATED, lines={"S": 1}) for _ in range(n)
    ]
    run = _runner(committed_db)
    results = await asyncio.gather(*(run(oid, f"k{i}") for i, oid in enumerate(order_ids)))

    allocated = [r for r in results if r.state is OrderState.ALLOCATED]
    backordered = [r for r in results if r.state is OrderState.BACKORDERED]
    assert len(allocated) == m
    assert len(backordered) == n - m
    async with committed_db.connect() as conn:
        reserved = (
            await conn.execute(
                select(func.coalesce(func.sum(reservation.c.qty), 0)).where(
                    reservation.c.sku_id == sku
                )
            )
        ).scalar_one()
    assert reserved == m  # no oversell: total reserved equals available
    await assert_invariants(committed_db, sku)


async def test_same_key_fired_concurrently_is_one_effect(committed_db: AsyncEngine) -> None:
    sku = await seed_sku_locations_stock(committed_db, "S", {"L1": 5})
    order_id = await seed_order(committed_db, state=OrderState.CREATED, lines={"S": 5})
    run = _runner(committed_db)
    k = 8
    results = await asyncio.gather(*(run(order_id, "same-key") for _ in range(k)))

    assert all(r == results[0] for r in results)  # all return the same effect
    async with committed_db.connect() as conn:
        count = (
            await conn.execute(
                select(reservation.c.reservation_id).where(reservation.c.order_id == order_id)
            )
        ).all()
    assert len(count) == 1  # exactly one reservation despite k concurrent calls
    await assert_invariants(committed_db, sku)


async def test_two_keys_same_order_one_allocates(committed_db: AsyncEngine) -> None:
    sku = await seed_sku_locations_stock(committed_db, "S", {"L1": 5})
    order_id = await seed_order(committed_db, state=OrderState.CREATED, lines={"S": 5})
    run = _runner(committed_db)
    outcomes = await asyncio.gather(
        run(order_id, "key-a"), run(order_id, "key-b"), return_exceptions=True
    )

    allocated = [
        o for o in outcomes if not isinstance(o, BaseException) and o.state is OrderState.ALLOCATED
    ]
    rejected = [o for o in outcomes if isinstance(o, IllegalTransition)]
    assert len(allocated) == 1  # exactly one command allocated the order
    assert len(rejected) == 1  # the loser retried, found it allocated, and was rejected
    async with committed_db.connect() as conn:
        final = (
            await conn.execute(
                select(orders.c.state, orders.c.version).where(orders.c.order_id == order_id)
            )
        ).one()
    assert final.state == "allocated" and final.version == 2  # allocated exactly once
    async with committed_db.connect() as conn:
        count = (
            await conn.execute(
                select(reservation.c.reservation_id).where(reservation.c.order_id == order_id)
            )
        ).all()
    assert len(count) == 1
    await assert_invariants(committed_db, sku)


async def test_two_keys_same_order_ample_stock_one_allocates(committed_db: AsyncEngine) -> None:
    """Ample-stock variant: on_hand > ordered so both racers can reserve stock.

    The original race-3 test uses tight stock (5-of-5) so the loser reserves 0
    and hits the clean CAS path.  When on_hand=10 and ordered=5, both A and B can
    each reserve 5 from the stock row — meaning the loser's add_allocated guard
    (allocated_qty + 5 <= ordered_qty=5) fires AFTER the winner has already
    committed, preventing allocated_qty from exceeding ordered_qty and avoiding a
    raw IntegrityError from ck_order_line_monotonic.  The loser retries, re-reads
    the order as ALLOCATED, and resolves to IllegalTransition.
    """
    sku = await seed_sku_locations_stock(committed_db, "S2", {"L1": 10})
    order_id = await seed_order(committed_db, state=OrderState.CREATED, lines={"S2": 5})
    run = _runner(committed_db)
    outcomes = await asyncio.gather(
        run(order_id, "ample-a"), run(order_id, "ample-b"), return_exceptions=True
    )

    allocated = [
        o for o in outcomes if not isinstance(o, BaseException) and o.state is OrderState.ALLOCATED
    ]
    rejected = [o for o in outcomes if isinstance(o, IllegalTransition)]
    assert len(allocated) == 1, f"expected 1 allocated, got: {outcomes}"
    assert len(rejected) == 1, f"expected 1 IllegalTransition, got: {outcomes}"

    async with committed_db.connect() as conn:
        final = (
            await conn.execute(
                select(orders.c.state, orders.c.version).where(orders.c.order_id == order_id)
            )
        ).one()
    assert final.state == "allocated"
    assert final.version == 2

    async with committed_db.connect() as conn:
        line_row = (
            await conn.execute(
                select(order_line.c.allocated_qty).where(order_line.c.order_id == order_id)
            )
        ).one()
    assert line_row.allocated_qty == 5, (
        f"allocated_qty must equal ordered_qty (5), got {line_row.allocated_qty}"
    )

    async with committed_db.connect() as conn:
        res_rows = (
            await conn.execute(
                select(reservation.c.reservation_id).where(reservation.c.order_id == order_id)
            )
        ).all()
    assert len(res_rows) == 1, f"expected exactly 1 reservation row, got {len(res_rows)}"

    await assert_invariants(committed_db, sku)
