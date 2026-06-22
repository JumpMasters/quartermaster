from __future__ import annotations

from loadtest.metrics import CommandSample, Outcome, percentile, summarize


def test_percentile_interpolates() -> None:
    values = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.0) == 0.0
    assert percentile(values, 1.0) == 4.0
    assert percentile(values, 0.5) == 2.0


def test_percentile_empty_is_zero() -> None:
    assert percentile([], 0.5) == 0.0


def test_summarize_tallies_outcomes_and_rates() -> None:
    samples = [
        CommandSample(Outcome.OK, 0.010, 0),
        CommandSample(Outcome.OK, 0.020, 2),
        CommandSample(Outcome.TRANSIENT, 0.030, 0),
        CommandSample(Outcome.RETRY_EXHAUSTED, 0.040, 5),
        CommandSample(Outcome.ERROR, 0.050, 0),
    ]
    m = summarize("guarded", samples, wall_seconds=2.0)
    assert m.strategy == "guarded"
    assert m.count == 5
    assert m.throughput == 2.5  # 5 / 2.0
    assert m.total_retries == 7
    assert m.retry_exhausted == 1
    assert m.transient == 1
    assert m.errors == 1
    assert m.p50_ms == 30.0  # median of 10..50 ms


def test_summarize_zero_wall_is_zero_throughput() -> None:
    m = summarize("x", [CommandSample(Outcome.OK, 0.01, 0)], wall_seconds=0.0)
    assert m.throughput == 0.0
