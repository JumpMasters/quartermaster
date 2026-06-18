"""Unit tests for the idempotency status vocabulary."""

from __future__ import annotations

from quartermaster.domain.idempotency import IdempotencyStatus


def test_status_values_are_their_wire_strings() -> None:
    assert IdempotencyStatus.PENDING == "pending"  # type: ignore[comparison-overlap]
    assert IdempotencyStatus.SUCCEEDED == "succeeded"  # type: ignore[comparison-overlap]
    assert IdempotencyStatus.REJECTED == "rejected"  # type: ignore[comparison-overlap]


def test_status_has_exactly_three_members() -> None:
    assert {s.value for s in IdempotencyStatus} == {"pending", "succeeded", "rejected"}
