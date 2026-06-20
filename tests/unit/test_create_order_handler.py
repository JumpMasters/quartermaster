"""Unit tests for the create_order handler over record-only fakes."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from quartermaster.application.handlers.create_order import run_create_order
from quartermaster.domain.errors import UnknownSku
from quartermaster.domain.idempotency import IdempotencyStatus
from quartermaster.domain.ids import IdempotencyKey, OrderId, SkuId
from quartermaster.domain.state_machines import OrderState
from tests.unit.fakes import (
    FakeCatalogRepo,
    FakeIdempotencyRepo,
    FakeOrderRepo,
    FakeUnitOfWork,
    fake_factory,
)

_OID = OrderId(UUID("00000000-0000-7000-8000-000000000001"))
_FIXED = datetime(2026, 6, 18, tzinfo=UTC)


def _clock() -> datetime:
    return _FIXED


def _mint() -> OrderId:
    return _OID


async def test_create_order_inserts_header_and_lines() -> None:
    orders = FakeOrderRepo()
    idem = FakeIdempotencyRepo()
    uow = FakeUnitOfWork(
        orders=orders, idempotency=idem, catalog=FakeCatalogRepo(known={SkuId("A"), SkuId("B")})
    )

    result = await run_create_order(
        fake_factory(uow),
        ((SkuId("A"), 5), (SkuId("B"), 2)),
        IdempotencyKey("k1"),
        now=_clock,
        new_order_id=_mint,
    )

    assert result.order_id == _OID
    assert result.state is OrderState.CREATED
    assert [(line.sku_id, line.ordered) for line in result.lines] == [("A", 5), ("B", 2)]

    ((order, lines),) = orders.inserted
    assert order.order_id == _OID and order.state is OrderState.CREATED and order.version == 1
    assert order.created_at == _FIXED
    assert [(line.sku_id, line.ordered, line.allocated) for line in lines] == [
        ("A", 5, 0),
        ("B", 2, 0),
    ]

    ((_key, status, _resp),) = idem.finalize_calls
    assert status is IdempotencyStatus.SUCCEEDED


async def test_create_order_unknown_sku_rejected() -> None:
    orders = FakeOrderRepo()
    idem = FakeIdempotencyRepo()
    uow = FakeUnitOfWork(
        orders=orders, idempotency=idem, catalog=FakeCatalogRepo(known={SkuId("A")})
    )

    with pytest.raises(UnknownSku):
        await run_create_order(
            fake_factory(uow),
            ((SkuId("A"), 1), (SkuId("B"), 1)),
            IdempotencyKey("k1"),
            now=_clock,
            new_order_id=_mint,
        )

    assert orders.inserted == []
    ((_key, status, _resp),) = idem.finalize_calls
    assert status is IdempotencyStatus.REJECTED
