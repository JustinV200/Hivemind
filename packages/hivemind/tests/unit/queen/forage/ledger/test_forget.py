"""Tests for ForageLedger.forget_cell: a Night Veil Cell's ledger rows leave with it.

Run over both stores: the Cell's capacity and hosting plan, its Warden's pool report and
ceilings, and any grant still held on it or by its Warden leave the live book and the store in
one call (a ledger restored from the store afterwards holds none of them either); another Cell's
rows are untouched, and forgetting again removes nothing. `rows_about` says which rows are the
Cell's, whichever way its Warden is known: by a member id, its pool report or its grant.

Fits into the Hive:
    Mirrors ForageLedger.forget_cell in src/hivemind/queen/forage/ledger/book.py, with the two
    `LedgerStore.forget` implementations it writes through (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ledger.book for forget_cell, rows_about and cell_rows.
    - .claude/codingrules.md section 12 for the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.forage import make_capacity, make_grant

from hivemind.common.sqlite import connect
from hivemind.forage.grant_state import GrantState
from hivemind.forage.models.pools import Ceilings, HostingPlan, SlotPlan, SourceChain
from hivemind.forage.slots import ModelSlot
from hivemind.queen.forage.ledger import (
    ForageLedger,
    InMemoryLedgerStore,
    LedgerStore,
    LocalPoolReport,
    SqliteLedgerStore,
)
from waggle.clock import FakeClock
from waggle.ids import CellId, WardenId, new_cell_id, new_task_id, new_warden_id

_CEILINGS = Ceilings(
    max_sub_bees=4, model_vram_bytes=0, model_disk_bytes=0, loadable_sources=(), exportable_seats=0
)


@dataclass(frozen=True, slots=True)
class _Cell:
    """One Cell's ids: the Cell and the Warden that runs on it."""

    cell_id: CellId
    warden_id: WardenId


@pytest.fixture(params=("memory", "sqlite"))
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> LedgerStore:
    """A LedgerStore of the parametrised kind."""
    if request.param == "memory":
        return InMemoryLedgerStore()
    return await SqliteLedgerStore.create(connect(tmp_path / "ledger.sqlite3"), FakeClock())


async def _book_a_cell(ledger: ForageLedger, clock: FakeClock) -> _Cell:
    """Give one fresh Cell every kind of row the ledger keys to a Cell or its Warden."""
    cell = _Cell(new_cell_id(clock), new_warden_id(clock))
    await ledger.report_capacity(cell.cell_id, make_capacity())
    await ledger.report_local_pool(
        LocalPoolReport(
            warden_id=cell.warden_id,
            cell_id=cell.cell_id,
            sub_bees_active=1,
            model_vram_bytes=0,
            model_disk_bytes=0,
            seats_exported=0,
        )
    )
    grant = make_grant(
        GrantState.ACTIVE, clock, cell_id=cell.cell_id, holder=cell.warden_id, task_id=None
    )
    await ledger.record_grant(grant)
    source = SourceChain(primary="src_primary")
    plan = HostingPlan(
        cell_id=cell.cell_id,
        slots=(SlotPlan(slot=ModelSlot.WORKER, chain=source),),
        default=source,
        reason="local model covers it",
    )
    await ledger.decisions.record_plan(plan)
    await ledger.decisions.record_ceilings(cell.warden_id, _CEILINGS)
    return cell


def _holds_any_of(ledger: ForageLedger, cell: _Cell) -> bool:
    """Whether the live book still holds any row keyed to `cell` or its Warden."""
    return (
        ledger.capacity_for(cell.cell_id) is not None
        or ledger.grants_for(cell.warden_id) != ()
        or ledger.decisions.plan_for(cell.cell_id) is not None
        or ledger.decisions.ceilings_for(cell.warden_id) is not None
        or cell.warden_id in ledger.rows_about(cell.cell_id, frozenset()).wardens
    )


async def test_forget_cell_leaves_no_row_of_the_cell_and_keeps_every_other(
    store: LedgerStore,
) -> None:
    clock = FakeClock()
    ledger = ForageLedger(store=store)
    night_veil, meadow = await _book_a_cell(ledger, clock), await _book_a_cell(ledger, clock)

    # Capacity, plan, pool report, ceilings and the grant.
    assert await ledger.forget_cell(night_veil.cell_id, frozenset()) == 5

    restored = ForageLedger(store=store)
    await restored.restore()
    for book in (ledger, restored):
        assert not _holds_any_of(book, night_veil)
        assert _holds_any_of(book, meadow)
    assert await ledger.forget_cell(night_veil.cell_id, frozenset()) == 0


async def test_rows_about_finds_the_cells_warden_by_member_report_or_grant() -> None:
    clock = FakeClock()
    ledger = ForageLedger()
    cell = await _book_a_cell(ledger, clock)
    named_only = new_warden_id(clock)  # Known to the purge alone: no report, no grant here.
    task = new_task_id(clock)
    grant = make_grant(GrantState.ACTIVE, clock, holder=named_only, task_id=task)
    await ledger.record_grant(grant)

    rows = ledger.rows_about(cell.cell_id, frozenset({named_only, task, cell.cell_id}))

    assert rows.wardens == {cell.warden_id, named_only}
    assert len(rows.grants) == 2 and grant.id in rows.grants
    assert rows.tasks == {task}
