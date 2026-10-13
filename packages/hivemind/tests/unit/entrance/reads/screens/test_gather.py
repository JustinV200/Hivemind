"""Test hivemind.entrance.reads.screens.gather: the reads several screens share.

Over the Queen's own in-memory stores: unfinished tasks come back whole; finished ones are found
through the trail, newest first; Alarm events fold into open Alarms placed on Cells; a Heartbeat
splits into a sample per bee; episodes and Cell Wax read back; hosting plans classify against the
Forage map; and an open lease's start is its newest leased edge.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/gather.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.cells import make_cell
from builders.entrance.screens import StaticMap, heartbeat, scene
from builders.forage import make_source
from builders.memory import make_cell_wax, make_episode
from builders.observation import after, make_cell_view, make_event

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import CellKind, LeaseState
from hivemind.entrance.reads.screens.gather import (
    alarm_views,
    hosting_for,
    lease_starts,
    recent_episodes,
    recent_finished,
    telemetry_of,
    unfinished_tasks,
    wax_notes,
)
from hivemind.forage import HostingPlan, SourceChain
from hivemind.memory import MemoryContext, WaxState, record_episode
from hivemind.observation import models_from
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_cell_id
from waggle.messages.supervision import WorkerState


async def test_unfinished_tasks_are_every_one_still_in_play() -> None:
    hive = scene(FakeClock())
    first, second = await hive.submit({"a": (), "b": ("a",)})
    await hive.place(first)

    tasks = await unfinished_tasks(hive.reads)

    assert {(task.id, task.status) for task in tasks} == {
        (first.id, TaskStatus.RUNNING),
        (second.id, TaskStatus.PENDING),
    }


async def test_finished_tasks_are_found_newest_first_through_the_trail() -> None:
    hive = scene(FakeClock())
    older, newer = await hive.submit({"a": (), "b": ()})
    for task in (older, newer):
        await hive.place(task)
        hive.clock.advance(5)
        await hive.finish(task)

    finished = await recent_finished(hive.reads)

    assert [task.id for task in finished] == [newer.id, older.id]
    assert await unfinished_tasks(hive.reads) == []


async def test_open_alarms_are_folded_and_placed_through_their_task_or_warden() -> None:
    hive = scene(FakeClock())
    [task] = await hive.submit({"a": ()})
    task = await hive.place(task)
    by_task, by_warden, resolved = (new_alarm_id(hive.clock) for _ in range(3))
    kind = {"kind": "WORKER_FAILED", "severity": "WARNING"}
    for alarm_id, actor, task_id in (
        (by_task, "system", task.id),
        (by_warden, hive.link.warden_id, None),
        (resolved, "system", None),
    ):
        await hive.record(
            make_event(
                "alarm.raised",
                alarm_id,
                after(1),
                actor=actor,
                payload={**kind, "task_id": task_id},
            )
        )
    await hive.record(
        make_event("alarm.resolved", resolved, after(2), payload={**kind, "task_id": None})
    )

    alarms = await alarm_views(hive.reads, [task])

    assert {alarm.id: alarm.cell_id for alarm in alarms} == {
        by_task: hive.link.cell.id,
        by_warden: hive.link.cell.id,
    }


async def test_a_heartbeat_splits_into_a_sample_per_bee() -> None:
    hive = scene(FakeClock())
    hive.board.record(
        hive.link.warden_id,
        heartbeat([("", WorkerState.RUNNING), ("", WorkerState.PAUSED)]),
        hive.clock.now(),
    )

    samples = telemetry_of(hive.reads)

    assert [(s.warden_id, s.worker_state) for s in samples] == [
        (hive.link.warden_id, None),
        (hive.link.warden_id, WorkerState.RUNNING),
        (hive.link.warden_id, WorkerState.PAUSED),
    ]


async def test_episodes_and_cell_wax_read_back() -> None:
    hive = scene(FakeClock())
    context = MemoryContext(hive.deps.memory, hive.deps.identity, hive.clock)
    old, new = make_episode(hive.clock, at=after(1)), make_episode(hive.clock, at=after(2))
    for record in (old, new):
        await record_episode(record, context)
    mine = make_cell_wax(hive.clock, cell_id=hive.link.cell.id)
    proposed = make_cell_wax(
        hive.clock,
        cell_id=hive.link.cell.id,
        state=WaxState.PROPOSED,
        decided_by=None,
        decided_at=None,
    )
    elsewhere = make_cell_wax(hive.clock, cell_id=new_cell_id(hive.clock))
    for note in (mine, proposed, elsewhere):
        await hive.write_wax(note)

    episodes = await recent_episodes(hive.reads, 10)
    written_here = await wax_notes(hive.reads, hive.link.cell.id, frozenset({WaxState.WRITTEN}))
    every_written = await wax_notes(hive.reads, None, frozenset({WaxState.WRITTEN}))

    assert [e.id for e in episodes] == [new.id, old.id]
    assert [n.id for n in written_here] == [mine.id]
    assert {n.id for n in every_written} == {mine.id, elsewhere.id}


async def test_hosting_classifies_the_plan_against_the_forage_map() -> None:
    clock = FakeClock()
    cell = make_cell(CellKind.REAL, clock)
    plan = HostingPlan(cell_id=cell.id, reason="test", default=SourceChain(primary="mine"))
    served = StaticMap((make_source(source_id="mine", host_cell_id=cell.id),))
    mapped = scene(clock, cell=cell, forage_map=served)
    unmapped = scene(FakeClock(), cell=cell)
    for hive in (mapped, unmapped):
        await hive.deps.ledger.decisions.record_plan(plan)
    view = make_cell_view(id=cell.id)

    assert models_from(cell.id, hosting_for(mapped.reads, [view])) == "local"
    # The same plan with no Forage map wired cannot say where "mine" runs.
    assert models_from(cell.id, hosting_for(unmapped.reads, [view])) == "unknown"


async def test_an_open_leases_start_is_its_newest_leased_edge() -> None:
    hive = scene(FakeClock())
    cell = make_cell_view(id=hive.link.cell.id, lease_state=LeaseState.OPEN)
    released = make_cell_view(lease_state=LeaseState.RELEASED)
    await hive.record(make_event("cell.leased", cell.id, after(10)))
    await hive.record(make_event("cell.leased", cell.id, after(30)))

    starts = await lease_starts(hive.reads, [cell, released])

    assert starts == {cell.id: after(30)}
