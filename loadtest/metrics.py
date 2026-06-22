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


_COLUMNS = (
    ("strategy", 10),
    ("thrpt/s", 10),
    ("p50ms", 8),
    ("p99ms", 8),
    ("retries", 8),
    ("exhaust", 8),
    ("oversell", 9),
    ("oracle", 7),
)


def report_to_dict(metrics: StrategyMetrics, oversell: int, oracle_ok: bool) -> dict[str, object]:
    """A JSON-serializable record for one strategy's row."""
    return {
        "strategy": metrics.strategy,
        "count": metrics.count,
        "throughput": metrics.throughput,
        "p50_ms": metrics.p50_ms,
        "p99_ms": metrics.p99_ms,
        "total_retries": metrics.total_retries,
        "retry_exhausted": metrics.retry_exhausted,
        "transient": metrics.transient,
        "errors": metrics.errors,
        "oversell": oversell,
        "oracle_ok": oracle_ok,
    }


def render_table(rows: list[tuple[StrategyMetrics, int, bool]]) -> str:
    """A fixed-width text table, one row per (metrics, oversell, oracle_ok)."""
    header = " ".join(name.rjust(width) for name, width in _COLUMNS)
    lines = [header, "-" * len(header)]
    for metrics, oversell, oracle_ok in rows:
        cells = (
            metrics.strategy,
            f"{metrics.throughput:.0f}",
            f"{metrics.p50_ms:.1f}",
            f"{metrics.p99_ms:.1f}",
            str(metrics.total_retries),
            str(metrics.retry_exhausted),
            str(oversell),
            "OK" if oracle_ok else "FAIL",
        )
        lines.append(
            " ".join(str(c).rjust(width) for c, (_, width) in zip(cells, _COLUMNS, strict=True))
        )
    return "\n".join(lines)
