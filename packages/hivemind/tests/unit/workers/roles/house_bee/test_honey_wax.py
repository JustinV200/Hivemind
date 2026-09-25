"""Tests for hivemind.workers.roles.house_bee.honey: retired Cell Wax deposits (roadmap 7.9a).

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/house_bee/honey.py (codingrules section 3), split by
    feature (14.2) from test_honey.py, which covers aged Bee Bread. Every note is walked through
    the memory module's own wax state machine and deposited into a real SQLite Honey Store.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.house_bee.honey for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.cells import make_cell
from builders.house_bee import (
    WAX_TEXT_CAP,
    RecordedCells,
    memory_context,
    open_honey_access,
    retire_wax,
    sweep_deps,
    sweep_window,
)
from builders.memory import make_wax_proposal_input

from hivemind.cell import CellKind, CombShieldLevel, HoneyClearance
from hivemind.honey_store import NectarOrigin
from hivemind.manifest import HoneyRipeningSection
from hivemind.memory.cell_wax import propose_wax, write_wax
from hivemind.workers.roles.house_bee import GatheredOn, deposit_retired_wax
from waggle.clock import FakeClock
from waggle.ids import new_warden_id
from waggle.messages.cell.wax import WaxDecision


async def test_deposit_retired_wax_deposits_a_cleared_note_as_its_cells_history(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    proposer = new_warden_id(clock)
    wax = await retire_wax(ctx, cell.id, proposer=proposer)

    deposited = await deposit_retired_wax(
        sweep_deps(ctx, harness.access, RecordedCells.of(cell)), sweep_window(clock)
    )

    assert deposited == 1
    (nectar,) = await harness.access.store.pending_nectar(10)
    assert nectar.origin is NectarOrigin.CELL_WAX
    assert nectar.scope == f"cell:{cell.id}"
    assert nectar.title == "Cell Wax CAUTION (CLEARED)"
    assert nectar.bee == proposer
    assert nectar.observed_at == wax.proposed_at
    assert nectar.clearance is HoneyClearance.C1
    content = (await harness.access.store.nectar_content(nectar.id)).decode()
    assert wax.text in content
    assert wax.reason in content
    assert proposer in content
    assert await harness.access.store.has_source(f"wax:{wax.id}")


async def test_deposit_retired_wax_deposits_an_expired_note_and_never_a_written_one(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    expired = await retire_wax(ctx, cell.id, proposer=new_warden_id(clock), expire=True)
    written = await write_wax(
        await propose_wax(make_wax_proposal_input(clock=clock, cell_id=cell.id), WAX_TEXT_CAP, ctx),
        WaxDecision.AUTOPILOT,
        "Still standing.",
        ctx,
    )

    deposited = await deposit_retired_wax(
        sweep_deps(ctx, harness.access, RecordedCells.of(cell)), sweep_window(clock)
    )

    assert deposited == 1
    (nectar,) = await harness.access.store.pending_nectar(10)
    assert nectar.title == "Cell Wax CAUTION (EXPIRED)"
    assert await harness.access.store.has_source(f"wax:{expired.id}")
    assert not await harness.access.store.has_source(f"wax:{written.id}")


async def test_deposit_retired_wax_never_deposits_the_same_note_twice(tmp_path: Path) -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await retire_wax(ctx, cell.id, proposer=new_warden_id(clock))
    deps = sweep_deps(ctx, harness.access, RecordedCells.of(cell))

    first = await deposit_retired_wax(deps, sweep_window(clock))
    second = await deposit_retired_wax(deps, sweep_window(clock))

    assert (first, second) == (1, 0)


async def test_deposit_retired_wax_labels_a_borrowed_cells_history_c2(tmp_path: Path) -> None:
    clock = FakeClock()
    device = make_cell(kind=CellKind.REAL, clock=clock)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await retire_wax(ctx, device.id, proposer=new_warden_id(clock))

    await deposit_retired_wax(
        sweep_deps(ctx, harness.access, RecordedCells.of(device)), sweep_window(clock)
    )

    (nectar,) = await harness.access.store.pending_nectar(10)
    assert nectar.clearance is HoneyClearance.C2


async def test_deposit_retired_wax_skips_a_night_veil_cells_wax(tmp_path: Path) -> None:
    clock = FakeClock()
    cell_id = make_cell(clock=clock).id
    veiled = GatheredOn(cell_id=cell_id, from_borrowed_cell=False, tier=CombShieldLevel.NIGHT_VEIL)
    ctx = memory_context(clock)
    harness = await open_honey_access(tmp_path, clock)
    await retire_wax(ctx, cell_id, proposer=new_warden_id(clock))

    deposited = await deposit_retired_wax(
        sweep_deps(ctx, harness.access, RecordedCells(cells={cell_id: veiled})), sweep_window(clock)
    )

    assert deposited == 0
    assert await harness.access.store.pending_nectar(10) == ()


async def test_deposit_retired_wax_keeps_to_the_per_sweep_budget(tmp_path: Path) -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    ctx = memory_context(clock)
    ripening = HoneyRipeningSection(max_bee_bread_per_sweep=1)
    harness = await open_honey_access(tmp_path, clock, ripening=ripening)
    for _ in range(2):
        await retire_wax(ctx, cell.id, proposer=new_warden_id(clock))
        clock.advance(1.0)
    deps = sweep_deps(ctx, harness.access, RecordedCells.of(cell))

    first = await deposit_retired_wax(deps, sweep_window(clock))
    second = await deposit_retired_wax(deps, sweep_window(clock))

    assert (first, second) == (1, 1)
