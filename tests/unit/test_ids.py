"""The typed identifiers are NewTypes: distinct to mypy, identity at runtime.

mypy --strict enforces distinctness (a bare str cannot be passed where a SkuId is
expected); these runtime checks only pin the identity behaviour and anchor
coverage of the module.
"""

from __future__ import annotations

from uuid import uuid4

from quartermaster.domain.ids import (
    IdempotencyKey,
    LocationId,
    MovementId,
    OrderId,
    ReceiptId,
    ReservationId,
    SkuId,
)


def test_string_ids_are_their_underlying_value() -> None:
    assert SkuId("WIDGET-1") == "WIDGET-1"
    assert LocationId("A-01-1") == "A-01-1"
    assert IdempotencyKey("idem-123") == "idem-123"


def test_uuid_ids_are_their_underlying_value() -> None:
    value = uuid4()
    assert OrderId(value) == value
    assert ReceiptId(value) == value
    assert ReservationId(value) == value
    assert MovementId(value) == value
