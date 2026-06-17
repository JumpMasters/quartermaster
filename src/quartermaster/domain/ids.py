"""Typed identifiers for the domain entities.

``NewType`` over primitives gives mypy ``--strict`` distinctness at zero runtime
cost: a ``SkuId`` is just a ``str`` at runtime, but the type checker rejects a
bare ``str`` — or a ``LocationId`` — where a ``SkuId`` is expected. Runtime
validation lives at the edges (Pydantic) and in the database (CHECK
constraints); the pure leaf only needs distinct names.

Natural codes (``SkuId``, ``LocationId``) wrap ``str`` — they *are* the identity,
matching the design spec §3 primary keys. Document identifiers wrap ``UUID``; the
generation strategy (v4/v7/bigint) is a persistence-layer concern, deferred.
``IdempotencyKey`` is the client-supplied command identity and doubles as
``Movement.command_id`` (what the load harness's "one effect per key" oracle
groups on).
"""

from __future__ import annotations

from typing import NewType
from uuid import UUID

SkuId = NewType("SkuId", str)
LocationId = NewType("LocationId", str)
OrderId = NewType("OrderId", UUID)
ReceiptId = NewType("ReceiptId", UUID)
ReservationId = NewType("ReservationId", UUID)
MovementId = NewType("MovementId", UUID)
IdempotencyKey = NewType("IdempotencyKey", str)
