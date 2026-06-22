"""Unit tests for the backorder-sweep pass (fakes; no DB)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from quartermaster.application.ports import UnitOfWork, UnitOfWorkFactory
from quartermaster.domain.ids import LocationId, MovementId, OrderId, ReservationId, SkuId
from quartermaster.domain.orders import Order, OrderLine
from quartermaster.domain.state_machines import OrderState
from quartermaster.workers.backorder_sweep import sweep_backorders
from tests.unit.fakes import FakeOrderRepo, FakeStockRepo, FakeUnitOfWork, fake_factory

_NOW = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)


def _ids() -> tuple[OrderId, ReservationId, MovementId]:
    return OrderId(uuid4()), ReservationId(uuid4()), MovementId(uuid4())


def _backordered_order(order_id: OrderId) -> Order:
    return Order(order_id=order_id, state=OrderState.BACKORDERED, version=1, created_at=_NOW)


def _line(order_id: OrderId, ordered: int) -> OrderLine:
    return OrderLine(
        order_id=order_id, sku_id=SkuId("S"), ordered=ordered, allocated=0, picked=0, shipped=0
    )


def _seq_factory(make_uow: Callable[[int], UnitOfWork]) -> UnitOfWorkFactory:
    """A factory that builds a fresh UoW per call (call 0 is the batch read, then attempts)."""
    calls = [0]

    def factory() -> UnitOfWork:
        uow = make_uow(calls[0])
        calls[0] += 1
        return uow

    return factory


class _BrokenOrderRepo(FakeOrderRepo):
    """An order repo whose ``get`` raises an unexpected (non-domain) error."""

    async def get(self, order_id: OrderId) -> Order | None:
        raise RuntimeError("unexpected adapter failure")


async def test_satisfiable_order_is_reallocated() -> None:
    order_id, _, _ = _ids()
    uow = FakeUnitOfWork(
        orders=FakeOrderRepo(
            order=_backordered_order(order_id), lines=[_line(order_id, 5)], backordered=[order_id]
        ),
        stock=FakeStockRepo(cells={(SkuId("S"), LocationId("L1")): 5}),
    )
    run = await sweep_backorders(
        fake_factory(uow),
        now=lambda: _NOW,
        new_reservation_id=lambda: ReservationId(uuid4()),
        new_movement_id=lambda: MovementId(uuid4()),
        batch_size=100,
    )

    assert run.scanned == 1 and run.allocated == 1
    assert run.still_backordered == 0 and run.errors == 0 and run.conflicts == 0


async def test_short_stock_stays_backordered() -> None:
    order_id, _, _ = _ids()
    uow = FakeUnitOfWork(
        orders=FakeOrderRepo(
            order=_backordered_order(order_id), lines=[_line(order_id, 5)], backordered=[order_id]
        ),
        stock=FakeStockRepo(cells={(SkuId("S"), LocationId("L1")): 2}),
    )
    run = await sweep_backorders(
        fake_factory(uow),
        now=lambda: _NOW,
        new_reservation_id=lambda: ReservationId(uuid4()),
        new_movement_id=lambda: MovementId(uuid4()),
        batch_size=100,
    )

    assert run.scanned == 1 and run.allocated == 0
    assert run.still_backordered == 1 and run.errors == 0 and run.conflicts == 0


async def test_order_changed_under_sweep_is_a_conflict_not_an_error() -> None:
    order_id, _, _ = _ids()
    changed = Order(order_id=order_id, state=OrderState.ALLOCATED, version=1, created_at=_NOW)
    uow = FakeUnitOfWork(
        orders=FakeOrderRepo(order=changed, lines=[_line(order_id, 5)], backordered=[order_id]),
        stock=FakeStockRepo(cells={(SkuId("S"), LocationId("L1")): 5}),
    )
    run = await sweep_backorders(
        fake_factory(uow),
        now=lambda: _NOW,
        new_reservation_id=lambda: ReservationId(uuid4()),
        new_movement_id=lambda: MovementId(uuid4()),
        batch_size=100,
    )

    # allocate raised IllegalTransition (order concurrently moved): benign contention.
    assert run.scanned == 1 and run.allocated == 0
    assert run.conflicts == 1 and run.errors == 0 and run.still_backordered == 0
    reservations = uow.reservations
    assert reservations.added == []  # type: ignore[attr-defined]


def _conflicting_attempt_uow(order_id: OrderId) -> FakeUnitOfWork:
    """A UoW whose allocate loses the order-header CAS, raising OccConflict."""
    return FakeUnitOfWork(
        orders=FakeOrderRepo(
            order=_backordered_order(order_id), lines=[_line(order_id, 5)], cas_result=False
        ),
        stock=FakeStockRepo(cells={(SkuId("S"), LocationId("L1")): 5}),
    )


def _success_attempt_uow(order_id: OrderId) -> FakeUnitOfWork:
    return FakeUnitOfWork(
        orders=FakeOrderRepo(order=_backordered_order(order_id), lines=[_line(order_id, 5)]),
        stock=FakeStockRepo(cells={(SkuId("S"), LocationId("L1")): 5}),
    )


async def test_transient_conflict_is_retried_in_tick() -> None:
    order_id, _, _ = _ids()

    def make_uow(call: int) -> FakeUnitOfWork:
        if call == 0:  # the batch read
            return FakeUnitOfWork(orders=FakeOrderRepo(backordered=[order_id]))
        if call == 1:  # first attempt loses the CAS
            return _conflicting_attempt_uow(order_id)
        return _success_attempt_uow(order_id)  # retry succeeds in the same tick

    run = await sweep_backorders(
        _seq_factory(make_uow),
        now=lambda: _NOW,
        new_reservation_id=lambda: ReservationId(uuid4()),
        new_movement_id=lambda: MovementId(uuid4()),
        batch_size=100,
    )

    # The in-tick retry cleared the conflict: a fulfilment, not a logged conflict.
    assert run.scanned == 1 and run.allocated == 1
    assert run.conflicts == 0 and run.errors == 0 and run.still_backordered == 0


async def test_persistent_conflict_exhausts_retries_as_conflict() -> None:
    order_id, _, _ = _ids()

    def make_uow(call: int) -> FakeUnitOfWork:
        if call == 0:
            return FakeUnitOfWork(orders=FakeOrderRepo(backordered=[order_id]))
        return _conflicting_attempt_uow(order_id)  # every attempt loses the CAS

    run = await sweep_backorders(
        _seq_factory(make_uow),
        now=lambda: _NOW,
        new_reservation_id=lambda: ReservationId(uuid4()),
        new_movement_id=lambda: MovementId(uuid4()),
        batch_size=100,
    )

    # Retries exhausted: a benign conflict left for the next tick, not a fault.
    assert run.scanned == 1 and run.allocated == 0
    assert run.conflicts == 1 and run.errors == 0 and run.still_backordered == 0


async def test_unexpected_error_is_counted_as_error() -> None:
    order_id, _, _ = _ids()

    def make_uow(call: int) -> FakeUnitOfWork:
        if call == 0:
            return FakeUnitOfWork(orders=FakeOrderRepo(backordered=[order_id]))
        return FakeUnitOfWork(orders=_BrokenOrderRepo())

    run = await sweep_backorders(
        _seq_factory(make_uow),
        now=lambda: _NOW,
        new_reservation_id=lambda: ReservationId(uuid4()),
        new_movement_id=lambda: MovementId(uuid4()),
        batch_size=100,
    )

    assert run.scanned == 1 and run.allocated == 0
    assert run.errors == 1 and run.conflicts == 0 and run.still_backordered == 0


async def test_no_backordered_orders() -> None:
    uow = FakeUnitOfWork(orders=FakeOrderRepo(backordered=[]))
    run = await sweep_backorders(
        fake_factory(uow),
        now=lambda: _NOW,
        new_reservation_id=lambda: ReservationId(uuid4()),
        new_movement_id=lambda: MovementId(uuid4()),
        batch_size=100,
    )
    assert run == run.__class__(scanned=0, allocated=0, still_backordered=0, errors=0)
