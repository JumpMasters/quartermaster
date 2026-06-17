"""A Reservation links an order to the stock it has claimed at a location; its
lifecycle is governed by RESERVATION_MACHINE (design spec §5.4). The record's one
structural invariant is that it reserves a positive quantity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from quartermaster.domain.errors import InvariantViolation
from quartermaster.domain.ids import LocationId, OrderId, ReservationId, SkuId
from quartermaster.domain.reservations import Reservation
from quartermaster.domain.state_machines import ReservationState


def reservation(qty: int, state: ReservationState = ReservationState.HELD) -> Reservation:
    return Reservation(
        reservation_id=ReservationId(uuid4()),
        order_id=OrderId(uuid4()),
        sku_id=SkuId("WIDGET-1"),
        location_id=LocationId("A-01-1"),
        qty=qty,
        state=state,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )


def test_reservation_holds_its_fields() -> None:
    res = reservation(qty=5)
    assert res.qty == 5
    assert res.state is ReservationState.HELD


@pytest.mark.parametrize("qty", [0, -1])
def test_reservation_rejects_non_positive_qty(qty: int) -> None:
    with pytest.raises(InvariantViolation):
        reservation(qty)


def test_reservation_is_immutable() -> None:
    res = reservation(qty=5)
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass raises FrozenInstanceError
        res.qty = 9  # type: ignore[misc]
