"""The ``create_order`` command handler and its convenience runner.

Creation is an uncontended insert, but it still flows through the transaction
envelope so a retried request replays one order (one server-generated id) rather
than creating duplicates. The handler validates that every line's SKU exists in
the catalog before inserting; an unknown SKU is a hard rejection (ADR-0004).
Time and id generation enter via injected seams.
"""

from __future__ import annotations

from collections.abc import Callable

from quartermaster.application.clock import Clock
from quartermaster.application.commands import CreateOrderCommand
from quartermaster.application.envelope import execute
from quartermaster.application.ports import UnitOfWork, UnitOfWorkFactory
from quartermaster.application.results import CreatedLine, CreateOrderResult
from quartermaster.domain.errors import UnknownSku
from quartermaster.domain.ids import IdempotencyKey, OrderId, SkuId
from quartermaster.domain.orders import Order, OrderLine
from quartermaster.domain.state_machines import OrderState


async def create_order(
    uow: UnitOfWork,
    command: CreateOrderCommand,
    *,
    now: Clock,
    new_order_id: Callable[[], OrderId],
) -> CreateOrderResult:
    """Create a new order (header + lines) in the ``created`` state."""
    skus = {sku for sku, _ in command.lines}
    missing = await uow.catalog.missing_skus(skus)
    if missing:
        listed = ", ".join(sorted(missing))
        raise UnknownSku(f"unknown sku(s): {listed}")

    order_id = new_order_id()
    order = Order(order_id=order_id, state=OrderState.CREATED, version=1, created_at=now())
    lines = [
        OrderLine(
            order_id=order_id,
            sku_id=sku,
            ordered=qty,
            allocated=0,
            picked=0,
            shipped=0,
        )
        for sku, qty in command.lines
    ]
    await uow.orders.insert_order(order, lines)
    return CreateOrderResult(
        order_id=order_id,
        state=OrderState.CREATED,
        lines=tuple(CreatedLine(sku, qty) for sku, qty in command.lines),
    )


async def run_create_order(
    uow_factory: UnitOfWorkFactory,
    lines: tuple[tuple[SkuId, int], ...],
    key: IdempotencyKey,
    *,
    now: Clock,
    new_order_id: Callable[[], OrderId],
) -> CreateOrderResult:
    """Build the command, bind the handler's seams, and run it through the envelope."""
    command = CreateOrderCommand(lines, key)

    async def handler(uow: UnitOfWork, cmd: CreateOrderCommand) -> CreateOrderResult:
        return await create_order(uow, cmd, now=now, new_order_id=new_order_id)

    return await execute(uow_factory, command, handler, CreateOrderResult.decode)
