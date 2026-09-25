"""Contract suite for MemoryStore.purge_night_veil: the Night Veil teardown's one memory delete.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Runs the `purge_night_veil` clause of the
    hivemind.memory.store.protocol.MemoryStore contract over both implementations that ship, as
    tests.contracts.test_memory_store_contract does for the rest: every episode record, Handoff,
    Bee Bread entry, note and Cell Wax row that names the Night Veil Cell or one of its members
    (as its key, or anywhere in its body) goes, with its taint label; every other row, and every
    pin, stays exactly as it was.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.store.night_veil and hivemind.memory.store.sqlite.night_veil for the two
      halves under test.
    - .claude/codingrules.md section 12 for the boundary the purge ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.memory import (
    make_bee_bread_entry,
    make_cell_wax,
    make_episode,
    make_handoff,
    make_note,
    make_pin,
    make_trigger_event,
)

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.memory.cell_wax import WaxState
from hivemind.memory.errors import TaintTargetNotFoundError
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.memory.store.protocol import MemoryStore
from hivemind.memory.store.sqlite import SqliteMemoryStore
from hivemind.memory.taint import TaintedKind, TaintMarker, TaintSource, TaintState, TaintTarget
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail, SqlitePheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import (
    CellId,
    TaskId,
    WardenId,
    new_cell_id,
    new_event_id,
    new_hive_id,
    new_node_id,
    new_task_id,
    new_warden_id,
)

_EVERY_WAX_STATE = frozenset(WaxState)


@dataclass(frozen=True, slots=True)
class _Veiled:
    """One Night Veil Cell, the task it ran and its Warden, plus an unrelated task and Cell."""

    cell: CellId
    task: TaskId
    warden: WardenId
    other_task: TaskId
    other_cell: CellId

    @property
    def ids(self) -> frozenset[str]:
        """What the teardown purge hands the memory side channel: the Cell and its members."""
        return frozenset({self.cell, self.task, self.warden})


@pytest.fixture(params=("memory", "sqlite"))
async def store(request: pytest.FixtureRequest, tmp_path: Path) -> MemoryStore:
    """A MemoryStore of the parametrised kind; SQLite on one tmp_path file, trail first."""
    clock = FakeClock()
    if request.param == "memory":
        return InMemoryMemoryStore(MemoryPheromoneTrail(clock))
    db_path = tmp_path / "hive.sqlite3"
    await SqlitePheromoneTrail.create(connect(db_path), clock)
    return await SqliteMemoryStore.create(connect(db_path), clock)


def _veiled(clock: FakeClock) -> _Veiled:
    """Fresh ids for the Night Veil work and for what must survive its purge."""
    return _Veiled(
        cell=new_cell_id(clock),
        task=new_task_id(clock),
        warden=new_warden_id(clock),
        other_task=new_task_id(clock),
        other_cell=new_cell_id(clock),
    )


def _event(clock: FakeClock, subject_id: str, kind: str) -> MemoryEvent:
    """A well-formed MemoryEvent about `subject_id`: what every store write records beside it."""
    return MemoryEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


async def _episodes(store: MemoryStore, clock: FakeClock, world: _Veiled) -> str:
    """Record three episodes: one naming the task, one by the Warden, one about neither.

    Returns:
        The kept episode's id.
    """
    about_task = make_episode(
        clock, trigger=make_trigger_event(summary=f"Task {world.task} finished.")
    )
    by_warden = make_episode(clock, principal=world.warden)
    kept = make_episode(clock, trigger=make_trigger_event(summary=f"Task {world.other_task}."))
    for record in (about_task, by_warden, kept):
        await store.put_episode(record, _event(clock, record.id, "memory.episode"))
    return kept.id


async def _rows(store: MemoryStore, clock: FakeClock, world: _Veiled) -> None:
    """Record a Handoff, Bee Bread entry, note and Cell Wax row on each side of the veil."""
    for task in (world.task, world.other_task):
        event_id = new_event_id(clock)
        checkpoint = _event(clock, event_id, "memory.checkpoint")
        await store.put_handoff(event_id, make_handoff(task_id=task), task, checkpoint)
        entry = make_bee_bread_entry(clock, task_id=task, ref_ids=(task,))
        await store.add_bee_bread_entry(
            entry, _event(clock, entry.id, "memory.bee_bread_deposited")
        )
    for note in (
        make_note(clock, author=world.warden),
        make_note(clock, text=f"Cell {world.cell} was slow."),
        make_note(clock),
    ):
        await store.add_note(note, _event(clock, note.id, "memory.note"))
    for cell in (world.cell, world.other_cell):
        wax = make_cell_wax(clock, cell_id=cell)
        await store.put_wax(wax, _event(clock, cell, "memory.wax_proposed"))


async def test_purge_removes_every_row_naming_the_cell_or_a_member_and_keeps_the_rest(
    store: MemoryStore,
) -> None:
    clock = FakeClock()
    world = _veiled(clock)
    kept_episode = await _episodes(store, clock, world)
    await _rows(store, clock, world)
    pin = make_pin(clock, text=f"Remember {world.cell}.")
    await store.add_pin(pin, _event(clock, pin.id, "memory.pinned"))

    removed = await store.purge_night_veil(world.ids)

    # Two episodes, one Handoff, one entry, two notes and one Cell Wax row.
    assert removed == 7
    episodes = await store.list_episodes(None, HoneyClearance.C2, 10)
    assert [record.id for record in episodes] == [kept_episode]
    assert await store.list_bee_bread_by_task(world.task, HoneyClearance.C2) == ()
    assert len(await store.list_bee_bread_by_task(world.other_task, HoneyClearance.C2)) == 1
    notes = await store.list_notes(None, HoneyClearance.C2, 10)
    assert [note.text for note in notes] == [make_note().text]
    assert await store.list_wax(world.cell, _EVERY_WAX_STATE, HoneyClearance.C2) == ()
    assert len(await store.list_wax(world.other_cell, _EVERY_WAX_STATE, HoneyClearance.C2)) == 1
    # A pin is the human's own, kept on purpose, whatever it names.
    assert await store.list_pins(HoneyClearance.C2) == (pin,)


async def test_a_purged_rows_taint_label_goes_with_it(store: MemoryStore) -> None:
    clock = FakeClock()
    world = _veiled(clock)
    record = make_episode(clock, principal=world.warden)
    await store.put_episode(record, _event(clock, record.id, "memory.episode"))
    target = TaintTarget(kind=TaintedKind.EPISODE, item_id=record.id)
    tainted = _event(clock, record.id, "memory.tainted")
    marker = TaintMarker(
        state=TaintState.TAINTED,
        source=TaintSource.ISOLATION,
        reason="Isolated on a Guard report.",
        event_id=tainted.id,
        at=tainted.at,
    )
    await store.write_taint(target, marker, tainted)

    assert await store.purge_night_veil(world.ids) == 1

    with pytest.raises(TaintTargetNotFoundError):
        await store.read_taintable(target)


async def test_purging_nothing_or_again_removes_nothing(store: MemoryStore) -> None:
    clock = FakeClock()
    world = _veiled(clock)
    await _rows(store, clock, world)

    assert await store.purge_night_veil(frozenset()) == 0
    assert await store.purge_night_veil(world.ids) == 5  # Handoff, entry, two notes, wax.
    assert await store.purge_night_veil(world.ids) == 0
