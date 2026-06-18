"""The idempotency-key status vocabulary (design spec §3, §5.1).

A committed ``idempotency_key`` row is always ``succeeded`` or ``rejected``.
``pending`` is written by the claim INSERT and is only ever an *uncommitted*
intermediate — every code path either ``finalize``s it to a terminal status
before commit or rolls the transaction back. It is in the value set because the
two-phase claim writes it; it is never observed on a committed row.
"""

from __future__ import annotations

from enum import StrEnum


class IdempotencyStatus(StrEnum):
    """Lifecycle of an idempotency-key row within one command transaction."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
