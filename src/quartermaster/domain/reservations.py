"""Stock reservations: the link between an order and the stock it has claimed.

Allocating an order creates ``held`` reservations (raising ``qty_reserved``
without physically moving stock). A reservation is later consumed by a pick,
released by an explicit cancel, or expired by the TTL reaper — its lifecycle is
governed by ``RESERVATION_MACHINE`` (design spec §5.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quartermaster.domain.errors import InvariantViolation
from quartermaster.domain.ids import LocationId, OrderId, ReservationId, SkuId
from quartermaster.domain.state_machines import ReservationState


@dataclass(frozen=True)
class Reservation:
    """An order's claim on ``qty`` of a SKU at a location, with a TTL."""

    reservation_id: ReservationId
    order_id: OrderId
    sku_id: SkuId
    location_id: LocationId
    qty: int
    state: ReservationState
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise InvariantViolation(f"reservation qty must be > 0, got {self.qty}")
