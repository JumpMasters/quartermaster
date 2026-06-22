"""The reservation-expiry reaper: a polled, idempotent, per-item pass.

Releases ``held`` reservations past their 15-minute ``expires_at`` and
**de-allocates the owning order**: state ``held -> expired``, the line's
``allocated_qty`` lowered, the order CASed ``allocated -> backordered`` (so the
backorder sweep re-allocates the freed stock), ``qty_reserved`` lowered, and an
``EXPIRE`` movement appended — one bounded transaction per reservation. It carries
no idempotency key: the reservation-state CAS (``held -> expired``) is the
exactly-once arbiter, so a concurrent reaper or an explicit ``cancel``/``pick``
racing the same row is a defined no-op, and the order flip is best-effort against
whatever state that race left (design §4, §5.4, §5.5; ADR-0018, ADR-0019).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from quartermaster.application.clock import Clock
from quartermaster.application.errors import OccConflict
from quartermaster.application.ports import UnitOfWorkFactory
from quartermaster.domain.errors import InvariantViolation
from quartermaster.domain.ids import IdempotencyKey, MovementId
from quartermaster.domain.movements import Movement, MovementType
from quartermaster.domain.state_machines import ReservationState
from quartermaster.workers.loop import ReaperRun

logger = logging.getLogger(__name__)


async def reap_reservations(
    uow_factory: UnitOfWorkFactory,
    *,
    now: Clock,
    new_movement_id: Callable[[], MovementId],
    batch_size: int,
) -> ReaperRun:
    """Expire up to ``batch_size`` due reservations, one transaction each."""
    async with uow_factory() as uow:
        due = await uow.reservations.due_for_expiry(now(), batch_size)

    acted = 0
    reopened = 0
    errors = 0
    conflicts = 0
    invariant_violations = 0
    for res in due:
        try:
            async with uow_factory() as uow:
                if not await uow.reservations.transition(
                    res.reservation_id, ReservationState.HELD, ReservationState.EXPIRED
                ):
                    continue  # another actor finalised it; defined no-op (design §4b)
                if not await uow.orders.remove_allocated(res.order_id, res.sku_id, res.qty):
                    raise InvariantViolation(
                        f"reservation {res.reservation_id} was held but its order line "
                        f"cannot be de-allocated"
                    )
                flipped = await uow.orders.mark_backordered(res.order_id)
                if not await uow.stock.release(res.sku_id, res.location_id, res.qty):
                    raise InvariantViolation(
                        f"reservation {res.reservation_id} was held but its stock is missing"
                    )
                await uow.movements.append(
                    Movement(
                        movement_id=new_movement_id(),
                        ts=now(),
                        type=MovementType.EXPIRE,
                        sku_id=res.sku_id,
                        from_location=res.location_id,
                        to_location=None,
                        qty=res.qty,
                        ref=res.order_id,
                        command_id=IdempotencyKey(f"reaper:expire:{res.reservation_id}"),
                    )
                )
                await uow.commit()
                acted += 1
                if flipped:
                    reopened += 1
        except OccConflict:
            # A pick/cancel racing this reservation formed the ABBA cycle Postgres
            # broke and engine.py translated (ADR-0019/0020). Benign contention:
            # the transaction rolled back on exit and a later tick retries cleanly.
            conflicts += 1
            logger.info(
                "reservation reaper conflict on %s; will retry next tick", res.reservation_id
            )
        except InvariantViolation:
            # The one runtime integrity breach the reaper is positioned to detect:
            # a held reservation whose backing stock/line cannot be unwound. Route
            # it to its own counter so a real corruption signal can escalate rather
            # than hide among routine races.
            invariant_violations += 1
            logger.error(
                "reservation reaper INVARIANT VIOLATION on %s", res.reservation_id, exc_info=True
            )
        except Exception:
            logger.exception("reservation reaper failed on %s", res.reservation_id)
            errors += 1

    return ReaperRun(
        scanned=len(due),
        acted=acted,
        reopened=reopened,
        errors=errors,
        conflicts=conflicts,
        invariant_violations=invariant_violations,
    )
