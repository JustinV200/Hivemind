"""Tests for the taint order: an isolation tells the Cell's Warden to taint its own memory store.

Roadmap step 10.6a (ADR-0035). A Virtual Cell's Warden keeps its memory inside the Cell, where the
Queen's label on the Hive's tables cannot reach, so isolating a Cell sends its Warden a
`CellTaintOrder` naming the very scope and cause she labelled with: the Cell's bees and tasks,
from the first cited event (or the isolation's start), caused by `cell.isolated`, which keeps that
instant. An order lost to a closed link is sent again when the Warden reattaches while the
isolation stands, and never once it is lifted. A Night Veil Cell's tasks and bees are named from
its segment, where alone they are recorded, and the Hive's tables get no label of it (codingrules
12: a label's `memory.tainted` record would outlive the Cell).

Fits into the Hive:
    Mirrors src/hivemind/queen/isolation/taint.py and its call from src/hivemind/queen/attach.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.isolation.taint, under test.
    - tests/unit/wardens/isolation for the Warden carrying the order out.
"""

from __future__ import annotations

from datetime import datetime

from builders.isolation import (
    isolation_site,
    make_guard_report,
    night_veil_cell,
    place_running,
    queen_order,
    tracked_virtual_cell,
    veil_cell,
)
from builders.queen import WardenEnd, make_queen_deps, make_warden_link

from hivemind.pheromone import TrailQuery, WorkerEvent
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.guard_requests import GuardDeps
from hivemind.queen.isolation import (
    ISOLATED_KIND,
    isolate_cell,
    lift_isolation,
    resend_taint_order,
)
from hivemind.queen.isolation.pause import SPAWNED_KIND
from hivemind.queen.queen import Queen
from waggle.clock import FakeClock
from waggle.ids import TaskId, WorkerId, new_event_id, new_worker_id, timestamp_of


async def _record_spawn(deps: QueenDeps, link: WardenLink, task_id: TaskId) -> WorkerId:
    """Record the Warden's `worker.spawned` for a bee on `task_id`, as its segment ships it."""
    bee = new_worker_id(deps.clock)
    event = WorkerEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=link.warden_id,
        kind=SPAWNED_KIND,
        subject_id=bee,
        payload={"task_id": task_id, "role": "DRONE"},
    )
    await deps.trail.record(event)
    return bee


async def _setup() -> tuple[QueenDeps, WardenLink, WardenEnd, FakeClock]:
    """A Queen's collaborators with one Warden on a tracked Virtual Cell; pauses never wait."""
    clock = FakeClock()
    egress, _backend, cell = await tracked_virtual_cell(clock)
    guard = GuardDeps(egress=egress, pause_timeout_s=0.0)
    deps, link, warden_end = make_queen_deps(clock, cell=cell, guard=guard)
    return deps, link, warden_end, clock


async def test_isolating_orders_the_warden_to_taint_with_the_same_scope_and_cause() -> None:
    deps, link, warden_end, clock = await _setup()
    suspect = new_event_id(clock)  # The first event the Guard report cites.
    clock.advance(5.0)
    task = await place_running(deps, link)
    bee = await _record_spawn(deps, link, task.id)
    report = make_guard_report(clock, cell_id=link.cell.id, event_ids=(suspect,))

    outcome = await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id, report))
    await warden_end.pump_until(lambda: bool(warden_end.taint_orders))

    [order] = warden_end.taint_orders
    assert (order.cell_id, order.cause_event_id) == (link.cell.id, outcome.event_id)
    assert order.suspect_at == timestamp_of(suspect)
    assert order.authors == (link.warden_id, bee)
    assert order.task_ids == (task.id,)
    assert report.id in order.reason
    # Kept on the state change, so a lost order can be sent again with the same instant.
    [isolated] = await deps.trail.query(TrailQuery(kind=ISOLATED_KIND))
    assert datetime.fromisoformat(str(isolated.payload["suspect_at"])) == order.suspect_at
    await warden_end.close()


async def test_a_night_veil_cells_order_names_what_its_segment_holds_and_labels_no_table() -> None:
    clock = FakeClock()
    guard = GuardDeps(pause_timeout_s=0.0)
    deps, link, warden_end = make_queen_deps(clock, cell=night_veil_cell(clock), guard=guard)
    deps, segments = veil_cell(deps, link.cell.id)
    task = await place_running(deps, link)
    bee = await _record_spawn(deps, link, task.id)  # Names the task: veiled with the Cell.

    outcome = await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id))
    await warden_end.pump_until(lambda: bool(warden_end.taint_orders))

    [order] = warden_end.taint_orders
    assert (order.authors, order.task_ids) == ((link.warden_id, bee), (task.id,))
    assert outcome.tainted_count == 0
    assert await deps.trail.query(TrailQuery(kind="memory.tainted")) == ()
    assert await segments.query(link.cell.id, TrailQuery(kind=SPAWNED_KIND)) != ()
    await warden_end.close()


async def test_an_isolation_citing_nothing_suspects_from_when_it_began() -> None:
    deps, link, warden_end, clock = await _setup()
    began = clock.now()

    await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id))
    await warden_end.pump_until(lambda: bool(warden_end.taint_orders))

    # The human's own judgement cites no event: what the paused bees checkpoint is covered too.
    assert warden_end.taint_orders[0].suspect_at == began
    await warden_end.close()


async def test_an_order_lost_to_a_closed_link_is_sent_again_when_the_warden_reattaches() -> None:
    deps, link, warden_end, clock = await _setup()
    await warden_end.close()  # The link is gone before the isolation reaches its Warden.
    outcome = await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id))
    back, back_end = make_warden_link(
        deps.identity.hive_id, link.warden_id, deps.identity.node_id, link.cell, clock
    )

    sent = await resend_taint_order(deps, back)
    await back_end.pump_until(lambda: bool(back_end.taint_orders))

    assert outcome.isolated and sent
    assert back_end.taint_orders[0].cause_event_id == outcome.event_id
    await back_end.close()


async def test_nothing_is_resent_once_the_isolation_is_lifted() -> None:
    deps, link, warden_end, _clock = await _setup()
    site = isolation_site(deps, link)
    await isolate_cell(site, queen_order(link.cell.id))
    await lift_isolation(site, link.cell.id, None)

    assert not await resend_taint_order(deps, link)
    await warden_end.close()


async def test_a_warden_attaching_to_an_isolated_cell_is_sent_its_order() -> None:
    deps, link, warden_end, clock = await _setup()
    outcome = await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id))
    await warden_end.close()
    queen = Queen(deps)
    back, back_end = make_warden_link(
        deps.identity.hive_id, link.warden_id, deps.identity.node_id, link.cell, clock
    )

    await queen.attach_warden(back)
    await back_end.pump_until(lambda: bool(back_end.taint_orders))

    assert back_end.taint_orders[0].cause_event_id == outcome.event_id
    await queen.stop()
    await back_end.close()
