"""The backorder fulfilment sweep: a polled worker that re-allocates backordered
orders FIFO by age, one bounded transaction per order.

It re-runs the standard isolated ``allocate`` (which already accepts a
``backordered`` source and reserves only each line's outstanding quantity), so
the sweep adds no allocation logic of its own. Like the reapers it bypasses the
idempotency envelope: the order-state CAS and the invariant-guarded conditional
reserve are the guards (design §4, §5.5; ADRs 0007, 0016, 0017, 0018). This is
what decouples inbound from outbound — ``putaway`` never re-allocates inline.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from quartermaster.application.clock import Clock
from quartermaster.application.commands import AllocateCommand
from quartermaster.application.envelope import MAX_OCC_RETRIES
from quartermaster.application.errors import OccConflict
from quartermaster.application.handlers.allocate import allocate
from quartermaster.application.ports import UnitOfWorkFactory
from quartermaster.application.results import AllocateResult
from quartermaster.domain.errors import IllegalTransition
from quartermaster.domain.ids import IdempotencyKey, MovementId, OrderId, ReservationId
from quartermaster.domain.state_machines import OrderState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepRun:
    """Telemetry for one bounded backorder-sweep pass.

    ``conflicts`` counts orders left for the next tick by benign contention -- a
    lost header CAS that survived the in-tick OCC retries, or an order another
    actor moved out from under allocate -- kept apart from ``errors`` so genuine
    faults are not buried under routine races (issue #66).
    """

    scanned: int = 0
    allocated: int = 0
    still_backordered: int = 0
    errors: int = 0
    conflicts: int = 0


async def _reallocate_with_retry(
    uow_factory: UnitOfWorkFactory,
    order_id: OrderId,
    *,
    now: Clock,
    new_reservation_id: Callable[[], ReservationId],
    new_movement_id: Callable[[], MovementId],
) -> AllocateResult:
    """Re-run ``allocate`` for one order with the envelope's bounded OCC retry.

    The sweep bypasses the idempotency envelope (the order-state CAS and the
    guarded conditional reserve are the arbiters, ADR-0016/0017), but that does not
    mean foregoing in-tick retry: a lost header CAS or a translated reaper deadlock
    is transient, and retrying immediately fills the order now instead of leaving it
    backordered for a whole interval. Routing through ``execute`` with the
    ``sweep:{order_id}`` key would instead cache the first SUCCEEDED response and
    replay a stale result after a later reaper de-allocation, so the loop is owned
    here. Each attempt opens a fresh transaction; exhaustion re-raises OccConflict.
    """
    for _attempt in range(MAX_OCC_RETRIES):
        try:
            async with uow_factory() as uow:
                result = await allocate(
                    uow,
                    AllocateCommand(order_id, IdempotencyKey(f"sweep:{order_id}")),
                    now=now,
                    new_reservation_id=new_reservation_id,
                    new_movement_id=new_movement_id,
                )
                await uow.commit()
            return result
        except OccConflict:
            continue  # rolled back on __aexit__; retry against a fresh transaction
    raise OccConflict(f"backorder sweep exhausted {MAX_OCC_RETRIES} OCC retries on {order_id}")


async def sweep_backorders(
    uow_factory: UnitOfWorkFactory,
    *,
    now: Clock,
    new_reservation_id: Callable[[], ReservationId],
    new_movement_id: Callable[[], MovementId],
    batch_size: int,
) -> SweepRun:
    """Re-allocate up to ``batch_size`` backordered orders, one transaction each."""
    async with uow_factory() as uow:
        order_ids = await uow.orders.backordered_orders(batch_size)

    allocated = 0
    still_backordered = 0
    errors = 0
    conflicts = 0
    for order_id in order_ids:
        try:
            result = await _reallocate_with_retry(
                uow_factory,
                order_id,
                now=now,
                new_reservation_id=new_reservation_id,
                new_movement_id=new_movement_id,
            )
        except OccConflict:
            # In-tick retries exhausted on a persistently contended header: benign,
            # left backordered for the next tick rather than logged as a fault.
            conflicts += 1
            logger.info(
                "backorder sweep exhausted OCC retries on %s; will retry next tick", order_id
            )
            continue
        except IllegalTransition:
            # Another actor moved the order out of an allocatable state (e.g. a
            # concurrent allocate or cancel): benign contention, not a fault.
            conflicts += 1
            logger.info("backorder sweep: order %s moved out from under allocate", order_id)
            continue
        except Exception:
            logger.exception("backorder sweep failed on %s", order_id)
            errors += 1
            continue
        if result.state is OrderState.ALLOCATED:
            allocated += 1
        else:
            still_backordered += 1

    return SweepRun(
        scanned=len(order_ids),
        allocated=allocated,
        still_backordered=still_backordered,
        errors=errors,
        conflicts=conflicts,
    )
