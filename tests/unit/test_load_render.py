from __future__ import annotations

from loadtest.metrics import StrategyMetrics, render_table, report_to_dict


def _m(name: str) -> StrategyMetrics:
    return StrategyMetrics(
        strategy=name,
        count=64,
        throughput=1234.5,
        p50_ms=1.2,
        p99_ms=9.9,
        total_retries=7,
        retry_exhausted=0,
        transient=3,
        errors=0,
    )


def test_render_table_has_a_row_per_strategy() -> None:
    table = render_table([(_m("naive"), 12, False), (_m("guarded"), 0, True)])
    assert "naive" in table
    assert "guarded" in table
    assert "FAIL" in table  # naive oracle_ok=False
    assert "OK" in table


def test_report_to_dict_is_json_safe() -> None:
    d = report_to_dict(_m("guarded"), oversell=0, oracle_ok=True)
    assert d["strategy"] == "guarded"
    assert d["oversell"] == 0
    assert d["oracle_ok"] is True
    assert d["throughput"] == 1234.5
