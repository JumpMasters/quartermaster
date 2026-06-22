"""The engine applies server-side statement and lock timeouts (issue #37).

Without these, a statement or a row-lock wait on a contended stock cell (the
``FOR UPDATE`` reserve, the ``add_on_hand`` ``ON CONFLICT``) can block
indefinitely behind a stuck transaction instead of failing fast. The values are
sent as asyncpg ``server_settings`` on every new connection, so every statement
the repositories run is bounded.
"""

from __future__ import annotations

from sqlalchemy import text

from quartermaster.adapters.postgres.engine import create_engine


async def test_engine_applies_statement_and_lock_timeouts(postgres_url: str) -> None:
    engine = create_engine(postgres_url, statement_timeout_ms=1234, lock_timeout_ms=567)
    try:
        async with engine.connect() as conn:
            statement_timeout = await conn.scalar(
                text("SELECT current_setting('statement_timeout')")
            )
            lock_timeout = await conn.scalar(text("SELECT current_setting('lock_timeout')"))
        assert statement_timeout == "1234ms"
        assert lock_timeout == "567ms"
    finally:
        await engine.dispose()
