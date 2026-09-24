"""Tests for hivemind.queen.ticks.housekeeping: the Honey half of the Queen's housekeeping tick.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/housekeeping.py (codingrules section 3). The Queen side is
    `builders.queen.make_queen_deps`; the Honey Store is real SQLite
    (`builders.house_bee.open_honey_access`). What the sweep's memory phases do is covered by
    tests/unit/workers/roles/house_bee/test_sweep.py; this module covers what housekeeping adds:
    the Cell records the deposits are attributed through, and the per-tick chunk expiry.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.housekeeping for the module under test.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

from builders.cells import make_cell, make_hive_stand_config, make_identity
from builders.honey import make_nectar_deposit
from builders.house_bee import open_honey_access
from builders.memory import make_handoff
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskOutcome, TaskStatus
from hivemind.cell import CellKind, CombShieldLevel, HoneyClearance
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cell.local import HiveStandSource
from hivemind.honey_store import DepositSource, HoneyAccess, NectarIntake
from hivemind.manifest import HoneyStoreSection
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.pheromone import MemoryPheromoneTrail
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.state import ClusterState
from hivemind.queen.ticks.housekeeping import (
    HIVE_STAND_SOURCE,
    QueenCellRecords,
    placed_cell,
    run_housekeeping,
)
from hivemind.workers.roles.house_bee import GatheredOn
from waggle.clock import FakeClock
from waggle.ids import TaskId, new_task_id, new_worker_id

_DAY_S = 86_400.0  # The manifest's default bee_bread_after_s.


async def _placed_task(deps: QueenDeps, link: WardenLink) -> TaskId:
    """Submit a one-task goal and place it on `link`'s Cell (PENDING -> ASSIGNED)."""
    (task,) = await deps.chamber.submit(make_graph_draft({"work": ()}))
    await deps.chamber.assign(task.id, link.warden_id, link.cell.id, "Placed for this test.")
    return task.id


class _CountingIntake(NectarIntake):
    """A real intake that counts how often the Queen's tick asked it to expire idle deposits."""

    def __init__(self, access: HoneyAccess, clock: FakeClock) -> None:
        default_label = HoneyClearance.from_wire(access.clearance.default_label)
        super().__init__(access.store, access.identity, clock, HoneyStoreSection(), default_label)
        self.expiries: list[datetime] = []

    def expire_groups(self, now: datetime) -> int:
        self.expiries.append(now)
        return super().expire_groups(now)


# ──────────────────────────────────────────────────────────────────────────────
# QueenCellRecords
# ──────────────────────────────────────────────────────────────────────────────


async def test_queen_cell_records_attribute_a_task_to_the_cell_it_was_placed_on() -> None:
    clock = FakeClock()
    device = make_cell(kind=CellKind.REAL, clock=clock)
    deps, link, _end = make_queen_deps(clock, cell=device)
    task_id = await _placed_task(deps, link)

    gathered = await QueenCellRecords(deps.chamber, deps.trail, [link]).gathered_on(task_id)

    assert gathered == GatheredOn(device.id, from_borrowed_cell=True, tier=CombShieldLevel.MEADOW)


async def test_queen_cell_records_find_a_finished_tasks_cell_from_the_chambers_record() -> None:
    # Completion clears Task.cell_id; the chamber's own task.assigned event still names the Cell.
    clock = FakeClock()
    device = make_cell(kind=CellKind.REAL, clock=clock)
    deps, link, _end = make_queen_deps(clock, cell=device)
    task_id = await _placed_task(deps, link)
    await deps.chamber.start(task_id)
    outcome = TaskOutcome(status=TaskStatus.SUCCEEDED, summary="Done.", verified_by=link.warden_id)
    await deps.chamber.complete(task_id, outcome)

    gathered = await QueenCellRecords(deps.chamber, deps.trail, [link]).gathered_on(task_id)

    assert (await deps.chamber.get(task_id)).cell_id is None
    assert gathered is not None
    assert gathered.cell_id == device.id


async def test_placed_cell_names_no_cell_for_a_task_never_placed() -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    (task,) = await deps.chamber.submit(make_graph_draft({"work": ()}))

    assert await placed_cell(deps.chamber, deps.trail, task.id) is None


async def test_queen_cell_records_attribute_task_less_material_to_the_hive_stand() -> None:
    clock = FakeClock()
    stand = make_cell(kind=CellKind.REAL, clock=clock, source=HIVE_STAND_SOURCE)
    deps, link, _end = make_queen_deps(clock, cell=stand)

    gathered = await QueenCellRecords(deps.chamber, deps.trail, [link]).gathered_on(None)

    assert gathered is not None
    assert gathered.cell_id == stand.id


async def test_queen_cell_records_name_no_cell_for_task_less_material_without_a_hive_stand() -> (
    None
):
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)  # A Cell from some other source.

    assert await QueenCellRecords(deps.chamber, deps.trail, [link]).gathered_on(None) is None


async def test_queen_cell_records_fall_back_to_the_hive_stand_for_an_unknown_task() -> None:
    clock = FakeClock()
    stand = make_cell(kind=CellKind.REAL, clock=clock, source=HIVE_STAND_SOURCE)
    deps, link, _end = make_queen_deps(clock, cell=stand)

    gathered = await QueenCellRecords(deps.chamber, deps.trail, [link]).gathered_on(
        new_task_id(clock)
    )

    assert gathered is not None
    assert gathered.cell_id == stand.id


def test_queen_cell_records_treat_a_detached_cell_as_borrowed() -> None:
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    gone = make_cell(kind=CellKind.VIRTUAL, clock=clock)

    gathered = QueenCellRecords(deps.chamber, deps.trail, [link]).for_cell(gone.id)

    assert gathered == GatheredOn.unrecorded(gone.id)


# ──────────────────────────────────────────────────────────────────────────────
# run_housekeeping: the Honey half
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_housekeeping_expires_abandoned_chunked_deposits_on_every_tick(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    counting = _CountingIntake(harness.access, clock)
    deps, link, _end = make_queen_deps(
        clock, honey=dataclasses.replace(harness.access, intake=counting)
    )
    # One chunk of a two-chunk deposit arrives, and its sender never sends the rest.
    first = make_nectar_deposit(clock=clock, cell_id=link.cell.id, final=False, total_bytes=64)
    source = DepositSource(
        sender=link.warden_id,
        cell_id=link.cell.id,
        from_borrowed_cell=True,
        tier=CombShieldLevel.MEADOW,
    )
    await counting.receive_chunk(first, source)
    state = ClusterState()

    await run_housekeeping(deps, [link], state)  # The first tick: nothing idle yet.
    clock.advance(120.0)  # Past the spec's sixty-second idle timeout.
    await run_housekeeping(deps, [link], state)

    assert len(counting.expiries) == 2
    assert counting.expire_groups(clock.now()) == 0  # Already dropped by the second tick.


async def test_run_housekeeping_deposits_aged_bee_bread_at_the_tasks_own_cell(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    device = make_cell(kind=CellKind.REAL, clock=clock)
    harness = await open_honey_access(tmp_path, clock)
    deps, link, _end = make_queen_deps(clock, cell=device, honey=harness.access)
    task_id = await _placed_task(deps, link)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=clock)
    worker = new_worker_id(clock)
    await write_checkpoint(make_handoff(task_id=task_id, written_by=worker), task_id, ctx)
    state = ClusterState()
    await run_housekeeping(deps, [link], state)  # Seeds the sweep timer.
    clock.advance(2 * _DAY_S)

    await run_housekeeping(deps, [link], state)  # Due: the sweep runs, the Honey half included.

    (nectar,) = await harness.access.store.pending_nectar(10)
    assert (nectar.task_id, nectar.cell_id, nectar.bee) == (task_id, device.id, worker)
    assert nectar.clearance is HoneyClearance.C2  # Gathered on a Real (borrowed) Cell.


async def test_run_housekeeping_touches_no_honey_store_when_none_is_wired() -> None:
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    task_id = await _placed_task(deps, link)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=clock)
    await write_checkpoint(make_handoff(task_id=task_id), task_id, ctx)
    state = ClusterState()
    await run_housekeeping(deps, [link], state)
    clock.advance(2 * _DAY_S)

    await run_housekeeping(deps, [link], state)  # Runs the memory phases only, as before.

    assert deps.housekeeping.last_sweep_at == clock.now()


async def test_hive_stand_source_matches_the_hive_stands_own_cell(tmp_path: Path) -> None:
    # The very string hivemind.cell.local stamps on the Hive Stand's one Cell, so the Queen's
    # records find it among her links.
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    config = make_hive_stand_config(tmp_path)
    source = HiveStandSource(
        config, make_identity(clock), trail, clock, InMemoryLeavingsStore(trail)
    )

    (cell,) = await source.cells()

    assert cell.source == HIVE_STAND_SOURCE
