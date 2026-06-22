"""CLI entry point for the full on-demand comparative sweep.

    uv run python -m loadtest --orders 2000 --concurrency 128 --out report.json

Prints the comparative table and (optionally) writes a JSON artifact. The
scaled-down deterministic run that gates CI lives in
``tests/integration/test_load_harness.py``; this is the full sweep (design spec §10).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from loadtest.harness import comparative_sweep
from loadtest.metrics import render_table, report_to_dict
from quartermaster.adapters.postgres.engine import create_engine


def _write_json(path: str, payload: list[dict[str, object]]) -> None:
    """Write the JSON report to *path* (sync helper; keeps the async fn clean)."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="loadtest", description="Comparative load sweep.")
    parser.add_argument("--database-url", default=None, help="async Postgres URL (asyncpg driver)")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--skus", type=int, default=8)
    parser.add_argument("--orders", type=int, default=512)
    parser.add_argument("--on-hand", type=int, default=16)
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=64)
    parser.add_argument("--dup", type=int, default=1)
    parser.add_argument("--out", default=None, help="write the JSON report here")
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.database_url is None:
        print("error: --database-url is required (async asyncpg URL)", file=sys.stderr)
        return 2
    engine = create_engine(args.database_url)
    try:
        reports = await comparative_sweep(
            engine,
            seed=args.seed,
            n_skus=args.skus,
            n_orders=args.orders,
            on_hand=args.on_hand,
            qty=args.qty,
            concurrency=args.concurrency,
            dup=args.dup,
        )
    finally:
        await engine.dispose()
    rows = [(r.metrics, r.oversell, r.oracle.ok) for r in reports]
    print(render_table(rows))
    if args.out is not None:
        payload = [report_to_dict(m, o, ok) for m, o, ok in rows]
        _write_json(args.out, payload)
        print(f"\nwrote {args.out}")
    # Non-zero exit if any non-naive strategy is dirty (naive is expected red).
    dirty = [r for r in reports if r.metrics.strategy != "naive" and not r.oracle.ok]
    return 1 if dirty else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
