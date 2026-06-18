"""Unit tests for app-side UUIDv7 generation."""

from __future__ import annotations

from uuid import UUID

from quartermaster.adapters.postgres.identifiers import (
    new_movement_id,
    new_order_id,
    new_reservation_id,
    new_uuid7,
)


def test_new_uuid7_is_version_7() -> None:
    assert new_uuid7().version == 7


def test_new_uuid7_values_are_distinct() -> None:
    ids = {new_uuid7() for _ in range(1000)}
    assert len(ids) == 1000


def test_new_uuid7_timestamp_prefix_is_non_decreasing() -> None:
    # The first 48 bits of a v7 UUID are a big-endian Unix-millisecond
    # timestamp; successive generations must be non-decreasing in that prefix.
    # The random tail bits within a single millisecond are deliberately not
    # asserted on, to avoid a flaky test.
    def ts_prefix(value: UUID) -> int:
        return value.int >> 80

    prefixes = [ts_prefix(new_uuid7()) for _ in range(1000)]
    assert prefixes == sorted(prefixes)


def test_typed_minters_return_version_7_uuids() -> None:
    assert new_order_id().version == 7
    assert new_reservation_id().version == 7
    assert new_movement_id().version == 7


def test_typed_minters_are_distinct_per_call() -> None:
    assert new_order_id() != new_order_id()
    assert new_reservation_id() != new_reservation_id()
    assert new_movement_id() != new_movement_id()
