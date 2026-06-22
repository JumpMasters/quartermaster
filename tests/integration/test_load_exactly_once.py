"""One idempotency key fired K times has exactly one effect (oracle NOT_CHECKED)."""

from __future__ import annotations

from loadtest.harness import assert_exactly_once
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_one_key_k_times_is_one_application(committed_db: AsyncEngine) -> None:
    result = await assert_exactly_once(committed_db, k=16, qty=5)
    assert result.reserved == 5  # the cell reflects exactly one reserve of qty
    assert result.movement_rows == 1  # exactly one RESERVE movement for the key
    assert result.reservation_rows == 1  # exactly one held reservation
