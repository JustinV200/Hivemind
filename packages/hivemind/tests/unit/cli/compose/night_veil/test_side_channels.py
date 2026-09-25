"""Tests for hivemind.cli.compose.night_veil.side_channels: the purge reaches every Hive store.

A Virtual side built from a manifest, as the composition root builds it, with its stores attached:
one Night Veil Cell ran one task, and every store besides the trail holds rows about it (a Queen
episode naming the task, the task's own words, the Cell's capacity and its Warden's pool report).
Its teardown purge leaves none of them, and counts every one in `cell.purged`; the same purge by a
process that never held the Cell's segment (the Cell's task still running when its Queen stopped)
still finds the task and the Warden through the chamber and the ledger, and cancels the task,
which can never finish now, before reducing it with the rest. Another Cell's rows stay.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/night_veil/side_channels.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose.night_veil.side_channels for the module under test.
    - tests.e2e.test_night_veil_boundary for the same end inside a whole running Hive.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from builders.forage import make_capacity
from builders.memory import make_episode, make_trigger_event
from builders.night_veil import night_veil_manifest
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, MemoryTaskStore, Task, TaskStatus
from hivemind.brood_chamber.store import SCRUBBED_TEXT
from hivemind.cell import CombShieldLevel, HoneyClearance, Isolation, TaskNeeds
from hivemind.cli.compose.night_veil import attach_side_channels
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.hive.night_veil import end_night_veil
from hivemind.memory import InMemoryMemoryStore, MemoryStore
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail, TaskEvent, TrailQuery
from hivemind.queen.forage.ledger import ForageLedger, LocalPoolReport
from waggle.clock import FakeClock
from waggle.ids import CellId, WardenId, new_cell_id, new_event_id, new_hive_id, new_node_id

_NIGHT_VEIL = CombShieldLevel.NIGHT_VEIL


@dataclass(frozen=True, slots=True)
class _Stores:
    """The two Hive stores the side channels take, beside the ledger."""

    chamber: BroodChamber
    memory: MemoryStore


@dataclass(frozen=True, slots=True)
class _Hive:
    """One Hive's Virtual side, its stores and ledger, over one durable trail."""

    clock: FakeClock
    durable: MemoryPheromoneTrail
    parts: VirtualCellsParts
    stores: _Stores
    ledger: ForageLedger
    tasks: MemoryTaskStore  # The chamber's own store, for seeding a task at any status.


def _hive(tmp_path: Path) -> _Hive:
    """Build the Virtual side from a manifest and attach its stores, as `build_hive` does."""
    clock = FakeClock()
    durable = MemoryPheromoneTrail(clock)
    manifest = night_veil_manifest(tmp_path)
    parts = build_virtual_cells(manifest, durable, clock)
    assert parts is not None
    identity = ChamberIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
    )
    tasks = MemoryTaskStore(durable)
    chamber = BroodChamber(tasks, clock, identity)
    stores = _Stores(chamber=chamber, memory=InMemoryMemoryStore(durable))
    ledger = ForageLedger()
    attach_side_channels(parts.night_veil, parts.registry, stores, ledger)
    return _Hive(clock, durable, parts, stores, ledger, tasks)


async def _worked(hive: _Hive, cell_id: CellId, warden_id: WardenId, *, held: bool) -> Task:
    """Leave the rows one Night Veil task on `cell_id` leaves in every store.

    Held, its Queen ran it to the end and filed it under the Cell as she went; otherwise its Queen
    stopped with it still running there, and nothing but the stores remembers it.
    """
    needs = TaskNeeds(comb_shield=_NIGHT_VEIL, isolation=Isolation.REQUIRED)
    spec = make_task_spec(needs=needs)
    task = (
        make_task(TaskStatus.SUCCEEDED, hive.clock, spec=spec)
        if held
        else make_task(
            TaskStatus.RUNNING, hive.clock, spec=spec, cell_id=cell_id, warden_id=warden_id
        )
    )
    event = _event(hive, TaskEvent, task.id, "task.submitted")
    await hive.tasks.insert_tasks([task], [event])
    episode = make_episode(hive.clock, trigger=make_trigger_event(summary=f"{task.id} is done."))
    await hive.stores.memory.put_episode(
        episode, _event(hive, MemoryEvent, episode.id, "memory.episode")
    )
    await hive.ledger.report_capacity(cell_id, make_capacity())
    await hive.ledger.report_local_pool(_report(cell_id, warden_id))
    if held:
        segments = hive.parts.night_veil.segments
        segments.open(cell_id)
        segments.bind(task.id, cell_id)
        segments.file(warden_id, cell_id)
    return task


def _report(cell_id: CellId, warden_id: WardenId) -> LocalPoolReport:
    """The pool report the Cell's Warden sent."""
    return LocalPoolReport(
        warden_id=warden_id,
        cell_id=cell_id,
        sub_bees_active=1,
        model_vram_bytes=0,
        model_disk_bytes=0,
        seats_exported=0,
    )


def _event[Event: (TaskEvent, MemoryEvent)](
    hive: _Hive, family: type[Event], subject_id: str, kind: str
) -> Event:
    """One well-formed event of `family` about `subject_id`."""
    return family(
        id=new_event_id(hive.clock),
        hive_id=new_hive_id(hive.clock),
        node_id=new_node_id(hive.clock),
        at=hive.clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


async def _purged_counts(hive: _Hive, cell_id: CellId) -> dict[str, object]:
    """The counts the purge's own `cell.purged` carries for `cell_id`."""
    [purged] = await hive.durable.query(TrailQuery(kind="cell.purged", subject_id=cell_id))
    return dict(purged.payload)


async def test_a_night_veil_teardown_leaves_no_row_of_the_cell_in_any_store(
    tmp_path: Path,
) -> None:
    hive = _hive(tmp_path)
    cell_id, warden_id = new_cell_id(hive.clock), WardenId("warden_01DXF6DT00CX1N2Z3Y4X5W6V7T")
    other, other_warden = new_cell_id(hive.clock), WardenId("warden_01DXF6DT00CX1N2Z3Y4X5W6V7S")
    task = await _worked(hive, cell_id, warden_id, held=True)
    await hive.ledger.report_capacity(other, make_capacity())
    await hive.ledger.report_local_pool(_report(other, other_warden))

    await end_night_veil(hive.parts.night_veil, cell_id, _NIGHT_VEIL)

    assert (await hive.stores.chamber.get(task.id)).spec.title == SCRUBBED_TEXT
    assert await hive.stores.memory.list_episodes(None, HoneyClearance.C2, 10) == ()
    assert hive.ledger.capacity_for(cell_id) is None
    assert hive.ledger.rows_about(cell_id, frozenset()).wardens == frozenset()
    assert hive.ledger.capacity_for(other) is not None  # Another Cell's rows stay.
    # The task, the episode, the capacity and the pool report: counts only, no content.
    assert await _purged_counts(hive, cell_id) == {
        "events_purged": 0,
        "side_channel_records_purged": 4,
    }


async def test_a_process_that_never_held_the_cell_still_finds_its_rows(tmp_path: Path) -> None:
    hive = _hive(tmp_path)
    cell_id, warden_id = new_cell_id(hive.clock), WardenId("warden_01DXF6DT00CX1N2Z3Y4X5W6V7T")
    task = await _worked(hive, cell_id, warden_id, held=False)

    # Known by its tier alone, as a restart's sweep or an offline Absconding knows it.
    await end_night_veil(hive.parts.night_veil, cell_id, _NIGHT_VEIL)

    # The chamber named its running task, so the Queen's episode about it went too.
    assert await hive.stores.memory.list_episodes(None, HoneyClearance.C2, 10) == ()
    assert hive.ledger.capacity_for(cell_id) is None
    # It could never finish without its Cell: cancelled, then reduced with the rest.
    ended = await hive.stores.chamber.get(task.id)
    assert (ended.status, ended.spec.title) == (TaskStatus.CANCELLED, SCRUBBED_TEXT)
