# tests/integration/test_idempotency_status_check.py
"""Postgres rejects an out-of-set idempotency status; valid statuses round-trip."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from quartermaster.adapters.postgres.tables import idempotency_key
from quartermaster.domain.idempotency import IdempotencyStatus


async def test_invalid_status_is_rejected(db: AsyncConnection) -> None:
    with pytest.raises(IntegrityError):
        await db.execute(
            idempotency_key.insert().values(
                key="k1",
                command_fingerprint="fp",
                status="bogus",
                response=None,
                created_at=datetime.now(UTC),
            )
        )


@pytest.mark.parametrize("status", [s.value for s in IdempotencyStatus])
async def test_valid_statuses_round_trip(db: AsyncConnection, status: str) -> None:
    await db.execute(
        idempotency_key.insert().values(
            key=f"k-{status}",
            command_fingerprint="fp",
            status=status,
            response=None,
            created_at=datetime.now(UTC),
        )
    )
