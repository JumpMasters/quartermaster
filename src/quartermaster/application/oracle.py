"""The offline invariant oracle (design spec §7, ADR-0011, ADR-0023).

A read-only, post-run audit. It reconstructs on-hand and reserved stock per
``(sku, location)`` cell from the append-only movement ledger and checks them
against the live ``stock`` and ``order_line`` tables, surfacing **torn / lost
writes** the storage CHECK constraints cannot catch — cases where one side of a
command persisted and the other did not, so the ledger and the live row disagree.
It never runs on the command path: no envelope, no idempotency, no commit.

It does **not** witness a *lockstep double-apply*: every handler drives the
guarded stock mutation and the appended movement from the same quantity in one
transaction, so a whole-command replay doubles the live balance *and* the ledger
reconstruction equally and conservation still agrees (and ``no_oversell`` need
not trip). Exactly-once is therefore strictly stronger than conservation and is
reported ``NOT_CHECKED`` here: the idempotency claim (``ON CONFLICT DO NOTHING``)
enforces it on the write path, and it must be asserted **directly** — fire one
idempotency key K times concurrently and assert both the resulting balance delta
and the per-``command_id`` movement-row count equal exactly one application —
which the concurrency tests and the load harness do, not this audit.

The reconstruction is a pure function of the ledger's (type, from, to, qty)
totals via this type->effect mapping:

    on_hand  += qty at ``to``  for RECEIVE, PUTAWAY ;  -= qty at ``from`` for PUTAWAY, PICK
    reserved += qty at ``to``  for RESERVE          ;  -= qty at ``from`` for RELEASE, EXPIRE, PICK

Keeping that arithmetic here (not in SQL) makes it exhaustively unit-testable
without a database; the adapter does only the GROUP BY totalling.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum, auto

from quartermaster.application.ports import (
    LineQuantities,
    MovementTotal,
    ReservedTotal,
    StockCell,
    UnitOfWorkFactory,
)
from quartermaster.domain.ids import LocationId, SkuId
from quartermaster.domain.movements import MovementType

Cell = tuple[SkuId, LocationId]


class CheckStatus(Enum):
    """Outcome of one oracle check."""

    OK = auto()
    FAILED = auto()
    NOT_CHECKED = auto()


@dataclass(frozen=True)
class Discrepancy:
    """One mismatch: at ``(sku_id, location_id)`` the oracle expected vs. observed."""

    sku_id: SkuId
    location_id: LocationId | None
    expected: int
    actual: int
    detail: str


@dataclass(frozen=True)
class CheckResult:
    """The outcome of a single named check and any discrepancies it found."""

    name: str
    status: CheckStatus
    discrepancies: tuple[Discrepancy, ...] = ()


@dataclass(frozen=True)
class OracleReport:
    """The result of one oracle run: one CheckResult per invariant."""

    checks: tuple[CheckResult, ...] = field(default=())

    @property
    def ok(self) -> bool:
        """True iff no check FAILED (a NOT_CHECKED check does not fail the report)."""
        return all(c.status is not CheckStatus.FAILED for c in self.checks)

    def check(self, name: str) -> CheckResult:
        """The CheckResult with ``name``; raises KeyError if absent."""
        for c in self.checks:
            if c.name == name:
                return c
        raise KeyError(name)


# type -> effect on the per-cell balances.
_ON_HAND_ADD = frozenset({MovementType.RECEIVE, MovementType.PUTAWAY})  # at `to`
_ON_HAND_SUB = frozenset({MovementType.PUTAWAY, MovementType.PICK})  # at `from`
_RESERVED_ADD = frozenset({MovementType.RESERVE})  # at `to`
_RESERVED_SUB = frozenset({MovementType.RELEASE, MovementType.EXPIRE, MovementType.PICK})  # `from`
# Types with no balance effect. Empty today (every type moves on-hand or reserved);
# it exists so the classification is *total* over MovementType, which
# test_oracle asserts -- a new type added without a deliberate effect (including
# "no effect") fails CI rather than silently folding to zero in reconstruct().
_NO_EFFECT: frozenset[MovementType] = frozenset()
_CLASSIFIED = _ON_HAND_ADD | _ON_HAND_SUB | _RESERVED_ADD | _RESERVED_SUB | _NO_EFFECT


def reconstruct(totals: Iterable[MovementTotal]) -> tuple[dict[Cell, int], dict[Cell, int]]:
    """Fold ledger totals into per-cell ``on_hand`` and ``reserved`` balances."""
    on_hand: dict[Cell, int] = defaultdict(int)
    reserved: dict[Cell, int] = defaultdict(int)
    for t in totals:
        if t.type in _ON_HAND_ADD and t.to_location is not None:
            on_hand[(t.sku_id, t.to_location)] += t.total_qty
        if t.type in _ON_HAND_SUB and t.from_location is not None:
            on_hand[(t.sku_id, t.from_location)] -= t.total_qty
        if t.type in _RESERVED_ADD and t.to_location is not None:
            reserved[(t.sku_id, t.to_location)] += t.total_qty
        if t.type in _RESERVED_SUB and t.from_location is not None:
            reserved[(t.sku_id, t.from_location)] -= t.total_qty
    return dict(on_hand), dict(reserved)


def _conservation(
    name: str, ledger: dict[Cell, int], actual: dict[Cell, int], detail: str
) -> CheckResult:
    discrepancies: list[Discrepancy] = []
    for cell in sorted(set(ledger) | set(actual)):
        expected = ledger.get(cell, 0)
        observed = actual.get(cell, 0)
        if expected != observed:
            sku, loc = cell
            discrepancies.append(Discrepancy(sku, loc, expected, observed, detail))
    status = CheckStatus.OK if not discrepancies else CheckStatus.FAILED
    return CheckResult(name, status, tuple(discrepancies))


def _no_oversell(
    totals: Iterable[MovementTotal], cells: Iterable[StockCell], shipped: dict[SkuId, int]
) -> CheckResult:
    """``shipped + on_hand_total <= ever_received`` per SKU (``ever_received = Σ RECEIVE``).

    Two limits, by design (ADR-0023):

    * **Not an independent witness.** In a consistent store this cannot fail unless
      ``conservation_on_hand`` ∧ ``state_integrity`` also fail; it is a sanity
      ceiling, not the detector. The real on-hand detection load sits on
      ``conservation_on_hand``.
    * **RMA-inflated; blind to duplicate-return phantom stock (#73).**
      ``ever_received`` counts *every* RECEIVE, including customer-RMA receives,
      so a return raises the ceiling in lockstep with the on-hand it adds.
      Restricting the ceiling to supplier receipts would be algebraically
      identical (returned units sit in ``on_hand_total`` too, so they would move
      to the left side unchanged), so it is left as-is. Consequence: N duplicate
      RMAs against one shipped order (ADR-0022 does not cap cumulative returns)
      manufacture real on-hand with matching RECEIVE rows, and this check stays
      green. The load harness must not rely on ``no_oversell`` to catch that; it
      caps one RMA per ``(origin_order, sku)`` instead.
    """
    received: dict[SkuId, int] = defaultdict(int)
    for t in totals:
        if t.type is MovementType.RECEIVE:
            received[t.sku_id] += t.total_qty
    on_hand_total: dict[SkuId, int] = defaultdict(int)
    for c in cells:
        on_hand_total[c.sku_id] += c.on_hand
    discrepancies: list[Discrepancy] = []
    for sku in sorted(set(received) | set(on_hand_total) | set(shipped)):
        ever = received.get(sku, 0)
        used = shipped.get(sku, 0) + on_hand_total.get(sku, 0)
        if used > ever:
            discrepancies.append(
                Discrepancy(sku, None, ever, used, "shipped+on_hand exceeds ever_received")
            )
    status = CheckStatus.OK if not discrepancies else CheckStatus.FAILED
    return CheckResult("no_oversell", status, tuple(discrepancies))


def _stock_bounds(cells: Iterable[StockCell]) -> CheckResult:
    discrepancies: list[Discrepancy] = []
    for c in cells:
        if not (0 <= c.reserved <= c.on_hand):
            discrepancies.append(
                Discrepancy(
                    c.sku_id, c.location_id, c.on_hand, c.reserved, "require 0<=reserved<=on_hand"
                )
            )
    status = CheckStatus.OK if not discrepancies else CheckStatus.FAILED
    return CheckResult("stock_bounds", status, tuple(discrepancies))


def _state_integrity(bad_lines: Iterable[LineQuantities]) -> CheckResult:
    discrepancies = tuple(
        Discrepancy(
            line.sku_id,
            None,
            line.ordered,
            line.shipped,
            f"order {line.order_id}: 0<=shipped({line.shipped})<=picked({line.picked})"
            f"<=allocated({line.allocated})<=ordered({line.ordered}) violated",
        )
        for line in bad_lines
    )
    status = CheckStatus.OK if not discrepancies else CheckStatus.FAILED
    return CheckResult("state_integrity", status, discrepancies)


def build_report(
    totals: list[MovementTotal],
    cells: list[StockCell],
    shipped: dict[SkuId, int],
    bad_lines: list[LineQuantities],
    held: list[ReservedTotal],
) -> OracleReport:
    """Run all checks over already-read store contents (pure; no I/O)."""
    on_hand_ledger, reserved_ledger = reconstruct(totals)
    on_hand_actual = {(c.sku_id, c.location_id): c.on_hand for c in cells}
    reserved_actual = {(c.sku_id, c.location_id): c.reserved for c in cells}
    held_by_cell: dict[Cell, int] = defaultdict(int)
    for h in held:
        held_by_cell[(h.sku_id, h.location_id)] += h.qty
    return OracleReport(
        checks=(
            _conservation("conservation_on_hand", on_hand_ledger, on_hand_actual, "on_hand"),
            _conservation("conservation_reserved", reserved_ledger, reserved_actual, "reserved"),
            # The reservation table vs stock.qty_reserved: an orphaned HELD reservation
            # (or reserved stock with no HELD backing) is invisible to the two ledger
            # reconstructions above, since RESERVE/RELEASE pairs can net out (issue #68).
            _conservation(
                "reservation_reconciliation",
                dict(held_by_cell),
                reserved_actual,
                "sum(held reservation.qty) vs stock.qty_reserved",
            ),
            _no_oversell(totals, cells, shipped),
            _stock_bounds(cells),
            _state_integrity(bad_lines),
            # NOT_CHECKED: conservation cannot witness a lockstep double-apply (see
            # the module docstring). Exactly-once is asserted directly by the
            # concurrency tests / load harness, not reconstructed here.
            CheckResult("exactly_once", CheckStatus.NOT_CHECKED),
        )
    )


async def run_oracle(uow_factory: UnitOfWorkFactory) -> OracleReport:
    """Read the store once (no commit) and return the invariant report.

    The five reads cross-check aggregates against each other, so they must fold
    over a single instant. Pass a snapshot-isolated factory
    (``postgres_read_uow_factory``, REPEATABLE READ): under the engine's default
    READ COMMITTED each statement takes a fresh snapshot, so a command committing
    between the ledger read and the stock read yields a torn cross-check -- a
    false FAIL on consistent data, or a real drift masked by a compensating pair
    (issue #70). Run against a quiesced store or under snapshot isolation; never
    on the command path (no envelope, no idempotency, no commit).
    """
    async with uow_factory() as uow:
        totals = await uow.movements.aggregate()
        cells = await uow.stock.all_cells()
        shipped = await uow.orders.shipped_by_sku()
        bad_lines = await uow.orders.lines_breaking_monotonic()
        held = await uow.reservations.held_totals()
    return build_report(totals, cells, shipped, bad_lines, held)
