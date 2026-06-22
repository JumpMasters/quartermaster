"""The async database engine factory.

The only place the SQLAlchemy async engine is constructed. It takes a database
URL (read from settings by the composition root in a later slice) and returns an
``AsyncEngine`` over asyncpg; the command path uses Core ``AsyncConnection``s,
not the ORM. Pool configuration stays at the library defaults for now.

A ``handle_error`` listener translates Postgres transient-conflict SQLSTATEs into
the application-level :class:`OccConflict` at this adapter boundary, so the
application layer never imports the SQLAlchemy/asyncpg error types and the
envelope's existing bounded OCC retry absorbs server-broken deadlocks and
serialization failures instead of letting them escape as an opaque 500.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from quartermaster.application.errors import OccConflict

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import ExceptionContext

# Postgres SQLSTATEs that mean "no work was committed, just run the transaction
# again" -- a deadlock the server broke to restore liveness (40P01) and a
# serialization failure (40001). That contract is exactly OccConflict's.
_TRANSIENT_CONFLICT_SQLSTATES = frozenset({"40P01", "40001"})


def _translate_transient_conflicts(context: ExceptionContext) -> None:
    """Re-raise Postgres deadlock/serialization failures as :class:`OccConflict`.

    Registered as a ``handle_error`` listener so it covers every statement the
    repositories execute against the engine. The command handlers and the
    reservation reaper take row locks in opposite orders (ADR-0019), so a
    ``pick``/``cancel`` racing the reaper can form an ABBA cycle that Postgres
    aborts with ``DeadlockDetected``; without this, the raw error escapes the
    envelope's ``except OccConflict`` and surfaces as a 500. asyncpg exposes the
    SQLSTATE as ``sqlstate``; the asyncpg dialect re-exposes it on the wrapped
    DBAPI error, so reading it off ``original_exception`` covers both.
    """
    sqlstate = getattr(context.original_exception, "sqlstate", None)
    if sqlstate in _TRANSIENT_CONFLICT_SQLSTATES:
        raise OccConflict(
            f"postgres transient conflict {sqlstate}; retry the transaction"
        ) from context.original_exception


def create_engine(
    database_url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: float = 30.0,
    pool_pre_ping: bool = True,
    statement_timeout_ms: int = 30000,
    lock_timeout_ms: int = 5000,
) -> AsyncEngine:
    """Build the async engine for ``database_url`` (a ``postgresql+asyncpg://`` URL).

    The isolation level is pinned to READ COMMITTED on the engine so the design's
    concurrency contract is self-enforcing rather than dependent on the Postgres
    cluster/role default. Every guard -- the conditional ``WHERE``, the
    ``FOR UPDATE`` EvalPlanQual re-read, the ``ON CONFLICT DO UPDATE`` re-read --
    is reasoned under READ COMMITTED (ADR-0005, ADR-0016); a stricter server
    default would otherwise turn clean guard-rejects into 40001 retries (issue
    #71). The pin applies on every new connection and is reset on return to pool.

    Pool sizing, ``pool_pre_ping``, and server-side ``statement_timeout`` /
    ``lock_timeout`` are configured rather than left at library defaults (issue
    #37): the OCC-retry envelope takes a fresh connection per attempt, so a
    too-small pool starves under contention; pre-ping discards a dropped backend
    before first use; and the timeouts bound a runaway query or a row-lock wait
    behind a stuck transaction so it fails fast instead of blocking indefinitely.
    The timeouts are sent as asyncpg ``server_settings`` (bare integers are
    milliseconds), so they apply to every statement on every connection. The
    composition root wires the values from :class:`Settings`; the defaults here
    mirror those so direct callers and tests get the same safe configuration.
    """
    engine = create_async_engine(
        database_url,
        isolation_level="READ COMMITTED",
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=pool_pre_ping,
        connect_args={
            "server_settings": {
                "statement_timeout": str(statement_timeout_ms),
                "lock_timeout": str(lock_timeout_ms),
            }
        },
    )
    event.listen(engine.sync_engine, "handle_error", _translate_transient_conflicts)
    return engine
