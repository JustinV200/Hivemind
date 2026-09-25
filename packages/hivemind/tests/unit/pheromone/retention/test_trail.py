"""Tests for hivemind.pheromone.retention.trail: VeiledTrail, the trail every Queen writer uses.

An event about no Night Veil Cell reaches the durable trail untouched; one about a living Night
Veil Cell reaches it only as its skeleton copy while the whole event waits in the Cell's segment;
one about a Cell already taken, or a task no Cell holds yet, reaches it only as its skeleton copy
and survives nowhere else. Reads and merges pass straight through; `query_cell` reads one Cell's
held segment beside the durable trail, as one answer in the trail's own order, cut to the limit.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/retention/trail.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention.trail for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from hivemind.pheromone import DuplicateEventError, PheromoneEvent, event_class_for
from hivemind.pheromone.retention import EphemeralSegments, VeiledTrail, query_cell
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery, TrailSegment
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_event_id, new_hive_id, new_node_id

_TASK = "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"
_REQUEST = "goalreq_01HZZZZZZZZZZZZZZZZZZZZZZZ"


class _Setup:
    """A durable memory trail, the segments and the VeiledTrail over both, plus one Cell."""

    def __init__(self) -> None:
        self.clock = FakeClock()
        self.durable = MemoryPheromoneTrail(self.clock)
        self.segments = EphemeralSegments(self.clock)
        self.trail = VeiledTrail(self.durable, self.segments)
        self.cell: CellId = new_cell_id(self.clock)
        self.hive_id = new_hive_id(self.clock)
        self.node = new_node_id(self.clock)

    def event(
        self, kind: str, subject_id: str, payload: dict[str, JsonValue] | None = None
    ) -> PheromoneEvent:
        """One valid event of `kind`'s family, on the Queen's node."""
        return event_class_for(kind)(
            id=new_event_id(self.clock),
            hive_id=self.hive_id,
            node_id=self.node,
            at=self.clock.now(),
            actor="system",
            kind=kind,
            subject_id=subject_id,
            payload=payload or {},
        )

    async def durable_events(self) -> tuple[PheromoneEvent, ...]:
        return await self.durable.query(TrailQuery())

    async def held_events(self) -> tuple[PheromoneEvent, ...]:
        return await self.segments.query(self.cell, TrailQuery())


async def test_an_event_about_no_night_veil_cell_reaches_the_trail_unchanged() -> None:
    setup = _Setup()
    event = setup.event("task.submitted", _TASK, {"title": "Write a haiku"})

    await setup.trail.record(event)

    assert await setup.durable_events() == (event,)


async def test_a_skeleton_kind_about_a_held_cell_crosses_cut_and_waits_whole() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    setup.segments.bind(_TASK, setup.cell)
    placed = setup.event("queen.placed", _TASK, {"reason": "fresh", "goal_request_id": _REQUEST})

    await setup.trail.record(placed)

    [durable] = await setup.durable_events()
    assert durable.id == placed.id and durable.payload == {"goal_request_id": _REQUEST}
    assert await setup.held_events() == (placed,)


async def test_any_other_kind_about_a_held_cell_waits_in_its_segment_only() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    provisioning = setup.event("cell.provisioning", setup.cell, {"backend": "docker"})

    await setup.trail.record(provisioning)

    assert await setup.durable_events() == ()
    assert await setup.held_events() == (provisioning,)


async def test_after_teardown_a_late_record_crosses_as_skeleton_or_not_at_all() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    await setup.segments.take(setup.cell)

    await setup.trail.record(setup.event("cell.destroying", setup.cell))
    await setup.trail.record(setup.event("cell.destroyed", setup.cell, {"grants_revoked": 1}))

    [destroyed] = await setup.durable_events()
    assert destroyed.kind == "cell.destroyed" and destroyed.payload == {"grants_revoked": 1}
    assert await setup.held_events() == ()


async def test_an_expected_tasks_planning_record_survives_nowhere() -> None:
    setup = _Setup()
    setup.segments.expect(_TASK)

    await setup.trail.record(setup.event("queen.planned", _TASK, {"goal_request_id": _REQUEST}))
    await setup.trail.record(setup.event("task.cancelled", _TASK, {"reason": "no Cell fits"}))

    [cancelled] = await setup.durable_events()
    assert cancelled.kind == "task.cancelled" and cancelled.payload == {}


async def test_a_refused_durable_write_keeps_nothing_in_the_segment() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    destroyed = setup.event("cell.destroyed", setup.cell)
    await setup.durable.record(destroyed)

    with pytest.raises(DuplicateEventError):
        await setup.trail.record(destroyed)

    assert await setup.held_events() == ()


async def test_reads_exports_and_merges_pass_straight_to_the_durable_trail() -> None:
    setup = _Setup()
    other_node = new_node_id(setup.clock)
    shipped = setup.event("warden.active", "warden_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    shipped = shipped.model_copy(update={"node_id": other_node})
    segment = TrailSegment(node_id=other_node, exported_at=setup.clock.now(), events=(shipped,))

    assert await setup.trail.merge_segment(segment) == 1

    assert await setup.trail.query(TrailQuery()) == (shipped,)
    assert (await setup.trail.export_segment(other_node)).events == (shipped,)
    assert setup.trail.durable is setup.durable


async def test_query_cell_reads_a_held_cells_segment_beside_the_durable_trail() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    first = setup.event("cell.isolated", setup.cell)  # Veiled: only in the Cell's segment.
    await setup.trail.record(first)
    setup.clock.advance(1.0)
    outside = setup.event("worker.paused", "worker_01HZZZZZZZZZZZZZZZZZZZZZZZ")
    await setup.trail.record(outside)  # About no Night Veil Cell: durable.
    setup.clock.advance(1.0)
    last = setup.event("cell.isolation_lifted", setup.cell)
    await setup.trail.record(last)

    newest = await query_cell(setup.trail, setup.cell, TrailQuery(newest_first=True, limit=2))
    everything = await query_cell(setup.trail, setup.cell, TrailQuery())

    assert await setup.trail.query(TrailQuery()) == (outside,)  # The plain read sees none of it.
    assert newest == (last, outside)
    assert everything == (first, outside, last)


async def test_query_cell_reads_the_trail_alone_for_any_cell_not_held() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    await setup.trail.record(setup.event("cell.isolated", setup.cell))
    other = new_cell_id(setup.clock)
    isolated = setup.event("cell.isolated", other)
    await setup.trail.record(isolated)

    assert await query_cell(setup.trail, other, TrailQuery()) == (isolated,)
    assert await query_cell(setup.durable, setup.cell, TrailQuery()) == (isolated,)
