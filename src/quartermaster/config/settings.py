"""Application settings loaded from the environment.

Minimal for the persistence slice: just the database URL the async engine needs.
The surface grows in later slices. Values come from ``QM_``-prefixed environment
variables (e.g. ``QM_DATABASE_URL``) and are validated at construction by
pydantic-settings.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration sourced from the environment."""

    model_config = SettingsConfigDict(env_prefix="QM_")

    database_url: str
    # Async engine pool and server-side timeouts (issue #37). The OCC-retry
    # envelope takes a fresh connection per attempt, so the pool is sized above
    # the library default to avoid starving under contention; pre-ping discards a
    # dropped backend before first use. statement/lock timeouts bound a runaway
    # query or a row-lock wait behind a stuck transaction so it fails fast
    # instead of hanging (the per-tick worker watchdog, #75, is the complement).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_s: float = 30.0
    db_pool_pre_ping: bool = True
    db_statement_timeout_ms: int = 30000
    db_lock_timeout_ms: int = 5000
    reservation_reaper_interval_s: float = 60.0
    idempotency_reaper_interval_s: float = 3600.0
    reaper_batch_size: int = 500
    idempotency_ttl_hours: int = 24
    backorder_sweep_interval_s: float = 30.0
    # Per-tick watchdog: a worker tick that overruns this is cancelled so a hung
    # query or lock wait cannot pin a worker indefinitely (issue #75). A generous
    # backstop above any healthy bounded-batch pass, not a tuning knob.
    worker_tick_timeout_s: float = 120.0
