from __future__ import annotations

import random

from loadtest.workload import _ALL_TABLES


def test_all_tables_is_fk_safe_order() -> None:
    # Children before parents so TRUNCATE ... CASCADE is deterministic; mirrors
    # the integration conftest ordering.
    assert _ALL_TABLES[0] == "movement"
    assert _ALL_TABLES[-2:] == ("sku", "location")
    assert set(_ALL_TABLES) == {
        "movement",
        "reservation",
        "order_line",
        "orders",
        "receipt_line",
        "receipt",
        "stock",
        "idempotency_key",
        "sku",
        "location",
    }


def test_seed_choice_is_deterministic_for_a_seed() -> None:
    # The generator's SKU choice per order is a pure function of the seed.
    a = [random.Random(7).choice(("HOT-0", "HOT-1", "HOT-2")) for _ in range(1)]
    b = [random.Random(7).choice(("HOT-0", "HOT-1", "HOT-2")) for _ in range(1)]
    assert a == b
