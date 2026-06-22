"""Unit tests for the async engine factory (no real connection)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

from quartermaster.adapters.postgres.engine import create_engine


async def test_create_engine_returns_async_engine_for_the_url() -> None:
    engine = create_engine("postgresql+asyncpg://u:p@localhost:5432/db")
    try:
        assert isinstance(engine, AsyncEngine)
        assert engine.url.drivername == "postgresql+asyncpg"
        assert engine.url.database == "db"
    finally:
        await engine.dispose()


async def test_create_engine_applies_pool_configuration() -> None:
    engine = create_engine(
        "postgresql+asyncpg://u:p@localhost:5432/db",
        pool_size=7,
        max_overflow=3,
        pool_timeout=11.0,
        pool_pre_ping=True,
    )
    try:
        pool = engine.pool
        assert isinstance(pool, QueuePool)
        assert pool.size() == 7
        assert pool.timeout() == 11.0
        # max_overflow and pre_ping are not exposed publicly; assert the stable
        # internals so a regression in the wiring is caught.
        assert pool._max_overflow == 3
        assert pool._pre_ping is True
    finally:
        await engine.dispose()
