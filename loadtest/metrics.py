"""Per-command samples and per-strategy aggregation for the harness report."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    """How one command finished, mirroring the envelope's classification."""

    OK = "ok"
    REJECTED = "rejected"  # hard business rejection (IllegalTransition, ...): expected
    TRANSIENT = "transient"  # InsufficientStock/StockConflict/QuantityCeilingExceeded
    RETRY_EXHAUSTED = "retry_exhausted"  # OCC retries exhausted -> would be a 503
    ERROR = "error"  # anything unexpected: a real failure


@dataclass(frozen=True)
class CommandSample:
    """One command's outcome, wall latency, and observed OCC-retry count."""

    outcome: Outcome
    latency_s: float
    retries: int


@dataclass(frozen=True)
class StrategyMetrics:
    """Aggregate over one strategy's run. Latencies are milliseconds."""

    strategy: str
    count: int
    throughput: float
    p50_ms: float
    p99_ms: float
    total_retries: int
    retry_exhausted: int
    transient: int
    errors: int


def percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated q-quantile (q in [0, 1]) of an ascending list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = q * (len(sorted_values) - 1)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def summarize(strategy: str, samples: list[CommandSample], wall_seconds: float) -> StrategyMetrics:
    """Fold per-command samples into one strategy's metrics."""
    latencies = sorted(s.latency_s for s in samples)
    count = len(samples)
    return StrategyMetrics(
        strategy=strategy,
        count=count,
        throughput=(count / wall_seconds) if wall_seconds > 0 else 0.0,
        p50_ms=percentile(latencies, 0.50) * 1000.0,
        p99_ms=percentile(latencies, 0.99) * 1000.0,
        total_retries=sum(s.retries for s in samples),
        retry_exhausted=sum(1 for s in samples if s.outcome is Outcome.RETRY_EXHAUSTED),
        transient=sum(1 for s in samples if s.outcome is Outcome.TRANSIENT),
        errors=sum(1 for s in samples if s.outcome is Outcome.ERROR),
    )
