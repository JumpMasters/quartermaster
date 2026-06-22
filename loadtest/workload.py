"""Seeded workload construction for the harness: bulk seeding + command thunks.

The *workload* is deterministic in the seed (which SKU each order wants); the
*interleaving* is not (design spec §6). The allocate thunk drives the envelope
directly so the harness can inject the counting ``sleep`` (retry instrumentation)
and a seeded ``rand`` (reproducible jitter) that the ``run_allocate`` convenience
wrapper does not forward (design spec §5).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from loadtest.runner import CommandThunk, Rand, Sleep
from quartermaster.adapters.postgres.identifiers import (
    new_movement_id,
    new_order_id,
    new_reservation_id,
)
from quartermaster.adapters.postgres.tables import (
    location,
    movement,
    order_line,
    orders,
    sku,
    stock,
)
from quartermaster.application.clock import system_clock
from quartermaster.application.commands import AllocateCommand
from quartermaster.application.envelope import execute
from quartermaster.application.handlers.allocate import allocate
from quartermaster.application.ports import UnitOfWork, UnitOfWorkFactory
from quartermaster.application.results import AllocateResult
from quartermaster.domain.ids import IdempotencyKey, OrderId, SkuId
from quartermaster.domain.state_machines import OrderState

# Children before parents: a TRUNCATE ... CASCADE order that mirrors the
# integration conftest's _ALL_TABLES. Harness-local so loadtest never imports tests.
_ALL_TABLES: tuple[str, ...] = (
    "movement",
    "reservation",
    "order_line",
    "orders",
    "receipt_line",
    "receipt",
    "stock",
    "idempotency_key",
    "sku",
    "location",
)


async def truncate_all(engine: AsyncEngine) -> None:
    """Reset every table so each strategy run starts from an empty store."""
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_ALL_TABLES)} RESTART IDENTITY CASCADE"))


@dataclass(frozen=True)
class ComparativeSeed:
    """The hot SKUs and the CREATED orders contending for them."""

    sku_ids: tuple[SkuId, ...]
    order_ids: tuple[OrderId, ...]


async def seed_comparative(
    engine: AsyncEngine,
    *,
    n_skus: int,
    n_orders: int,
    on_hand_per_cell: int,
    qty_per_order: int,
    rng: random.Random,
) -> ComparativeSeed:
    """Seed ``n_skus`` hot cells with scarce on-hand and ``n_orders`` CREATED orders.

    Each order wants ``qty_per_order`` of one randomly-chosen hot SKU. Scarce
    ``on_hand_per_cell`` relative to demand concentrates contention on the cells.
    """
    sku_ids = tuple(SkuId(f"HOT-{i}") for i in range(n_skus))
    loc_ids = tuple(f"S{i}" for i in range(n_skus))
    order_ids: list[OrderId] = []
    async with engine.begin() as conn:
        for s in sku_ids:
            await conn.execute(sku.insert().values(sku_id=s, description="hot", unit="each"))
        for loc in loc_ids:
            await conn.execute(location.insert().values(location_id=loc, kind="shelf"))
        for s, loc in zip(sku_ids, loc_ids, strict=True):
            await conn.execute(
                stock.insert().values(
                    sku_id=s, location_id=loc, qty_on_hand=on_hand_per_cell, qty_reserved=0
                )
            )
            # Synthetic RECEIVE movement so the oracle's on-hand ledger reconstruction
            # agrees with the seeded stock row. Without this, conservation_on_hand
            # always fails (ledger sees 0; stock table sees on_hand_per_cell).
            await conn.execute(
                movement.insert().values(
                    movement_id=new_movement_id(),
                    ts=datetime.now(UTC),
                    type="receive",
                    sku_id=s,
                    from_location=None,
                    to_location=loc,
                    qty=on_hand_per_cell,
                    ref=new_movement_id(),  # synthetic ref UUID; no FK on movement.ref
                    command_id=f"seed-receive-{s}-{loc}",
                )
            )
        for _ in range(n_orders):
            oid = new_order_id()
            chosen = rng.choice(sku_ids)
            await conn.execute(
                orders.insert().values(
                    order_id=oid,
                    state=OrderState.CREATED.value,
                    version=1,
                    created_at=datetime.now(UTC),
                )
            )
            await conn.execute(
                order_line.insert().values(
                    order_id=oid,
                    sku_id=chosen,
                    ordered_qty=qty_per_order,
                    allocated_qty=0,
                    picked_qty=0,
                    shipped_qty=0,
                )
            )
            order_ids.append(oid)
    return ComparativeSeed(sku_ids=sku_ids, order_ids=tuple(order_ids))


def allocate_thunk(
    uow_factory: UnitOfWorkFactory, order_id: OrderId, key: IdempotencyKey
) -> CommandThunk:
    """An allocate command bound to the envelope, accepting injected sleep/rand."""

    async def thunk(sleep: Sleep, rand: Rand) -> AllocateResult:
        command = AllocateCommand(order_id, key)

        async def handler(uow: UnitOfWork, cmd: AllocateCommand) -> AllocateResult:
            return await allocate(
                uow,
                cmd,
                now=system_clock,
                new_reservation_id=new_reservation_id,
                new_movement_id=new_movement_id,
            )

        return await execute(
            uow_factory, command, handler, AllocateResult.decode, sleep=sleep, rand=rand
        )

    return thunk
