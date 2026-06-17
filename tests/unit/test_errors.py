"""The domain error hierarchy is coherent: every error is a QuartermasterError."""

from __future__ import annotations

import pytest

from quartermaster.domain.errors import (
    IdempotencyKeyReuse,
    IllegalTransition,
    InsufficientStock,
    InvariantViolation,
    QuartermasterError,
)

DOMAIN_ERRORS: list[type[QuartermasterError]] = [
    InvariantViolation,
    IllegalTransition,
    InsufficientStock,
    IdempotencyKeyReuse,
]


@pytest.mark.parametrize("error_type", DOMAIN_ERRORS)
def test_domain_errors_subclass_base(error_type: type[QuartermasterError]) -> None:
    assert issubclass(error_type, QuartermasterError)


@pytest.mark.parametrize("error_type", DOMAIN_ERRORS)
def test_domain_errors_are_catchable_as_base(error_type: type[QuartermasterError]) -> None:
    with pytest.raises(QuartermasterError):
        raise error_type("boom")
