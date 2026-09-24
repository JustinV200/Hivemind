"""Tests for hivemind.pheromone.retention.segments: EphemeralSegments, the Queen-side segments.

Covers which Cell an event is about (its subject, an id anywhere in its payload's top level, an id
filed since), holding a living Cell's records and shipped segments, taking a segment whole at
teardown, and the index outliving the segment so a late record stays veiled.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/retention/segments.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention.segments for the module under test.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.pheromone import PheromoneEvent, event_class_for
from hivemind.pheromone.retention import EphemeralSegments, Veiling
from hivemind.pheromone.trail.protocol import TrailQuery, TrailSegment
from waggle.clock import FakeClock
from waggle.ids import CellId, NodeId, new_cell_id, new_event_id, new_hive_id, new_node_id

_TASK = "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"
_WARDEN = "warden_01HZZZZZZZZZZZZZZZZZZZZZZZ"
_GRANT = "grant_01HZZZZZZZZZZZZZZZZZZZZZZZ"


class _Setup:
    """A clock, a store and one Cell id, shared by a test's events."""

    def __init__(self) -> None:
        self.clock = FakeClock()
        self.segments = EphemeralSegments(self.clock)
        self.cell: CellId = new_cell_id(self.clock)
        self.queen_node: NodeId = new_node_id(self.clock)
        self.hive_id = new_hive_id(self.clock)

    def event(
        self,
        kind: str,
        subject_id: str,
        payload: dict[str, JsonValue] | None = None,
        node_id: NodeId | None = None,
    ) -> PheromoneEvent:
        """One valid event of `kind`'s family, on the Queen's node unless told otherwise."""
        return event_class_for(kind)(
            id=new_event_id(self.clock),
            hive_id=self.hive_id,
            node_id=node_id if node_id is not None else self.queen_node,
            at=self.clock.now(),
            actor="system",
            kind=kind,
            subject_id=subject_id,
            payload=payload or {},
        )

    def segment(self, node: NodeId, count: int) -> TrailSegment:
        """A segment of `count` events the Cell's own Warden recorded on `node`."""
        events = tuple(self.event("warden.active", _WARDEN, node_id=node) for _ in range(count))
        return TrailSegment(node_id=node, exported_at=self.clock.now(), events=events)


def test_an_event_is_about_a_cell_by_its_subject_or_any_payload_id() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    setup.segments.file(_WARDEN, setup.cell)
    held = Veiling(cell_id=setup.cell, held=True)

    assert setup.segments.veiling(setup.event("cell.ready", setup.cell)) == held
    assert setup.segments.veiling(setup.event("warden.spawned", _WARDEN)) == held
    by_payload = setup.event("queen.assigned", _TASK, {"cell_id": setup.cell})
    assert setup.segments.veiling(by_payload) == held
    by_list = setup.event("queen.decided", _TASK, {"cells": [setup.cell]})
    assert setup.segments.veiling(by_list) == held
    assert setup.segments.veiling(setup.event("cell.ready", new_cell_id(setup.clock))) is None


def test_an_expected_task_is_veiled_before_any_cell_holds_it() -> None:
    setup = _Setup()
    setup.segments.expect(_TASK)

    veiling = setup.segments.veiling(setup.event("queen.planned", _TASK, {"task_count": 1}))

    assert veiling == Veiling(cell_id=None, held=False)


async def test_a_held_cell_keeps_its_records_whole_and_queryable() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    event = setup.event("cell.provisioning", setup.cell, {"backend": "fake"})
    veiling = setup.segments.veiling(event)
    assert veiling is not None

    assert await setup.segments.keep(veiling, event) is True

    [kept] = await setup.segments.query(setup.cell, TrailQuery())
    assert kept == event


async def test_a_veiled_record_files_its_grant_so_a_later_one_is_veiled_too() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    setup.segments.bind(_TASK, setup.cell)
    granted = setup.event("forage.granted", _GRANT, {"task_id": _TASK})
    veiling = setup.segments.veiling(granted)
    assert veiling is not None

    await setup.segments.keep(veiling, granted)

    revoked = setup.event("forage.revoked", _GRANT, {"reason": "expired"})
    assert setup.segments.veiling(revoked) == Veiling(cell_id=setup.cell, held=True)


async def test_a_cells_shipped_segment_is_held_and_its_node_filed() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    node = new_node_id(setup.clock)

    assert await setup.segments.merge(setup.cell, setup.segment(node, 2)) == 2

    assert len(await setup.segments.query(setup.cell, TrailQuery())) == 2
    leased = setup.event("cell.leased", node)
    assert setup.segments.veiling(leased) == Veiling(cell_id=setup.cell, held=True)


async def test_a_segment_from_a_cell_this_store_never_held_is_not_its_to_merge() -> None:
    setup = _Setup()

    merged = await setup.segments.merge(setup.cell, setup.segment(new_node_id(setup.clock), 1))

    assert merged is None


async def test_take_returns_every_event_past_any_query_limit_and_the_cells_own_nodes() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    node = new_node_id(setup.clock)
    await setup.segments.merge(setup.cell, setup.segment(node, 1_050))
    queen_record = setup.event("cell.ready", setup.cell)
    await setup.segments.keep(Veiling(cell_id=setup.cell, held=True), queen_record)

    taken = await setup.segments.take(setup.cell)

    assert len(taken.events) == 1_051
    assert taken.node_ids == frozenset({node})
    assert not setup.segments.holds(setup.cell)
    assert setup.segments.owns(setup.cell)


async def test_a_taken_cell_is_never_held_again_and_its_late_records_are_withheld() -> None:
    setup = _Setup()
    setup.segments.open(setup.cell)
    setup.segments.file(_WARDEN, setup.cell)
    await setup.segments.take(setup.cell)

    setup.segments.open(setup.cell)
    late = setup.event("warden.stopped", _WARDEN)
    veiling = setup.segments.veiling(late)

    assert not setup.segments.holds(setup.cell)
    assert veiling == Veiling(cell_id=setup.cell, held=False)
    assert await setup.segments.keep(veiling, late) is False
    assert await setup.segments.merge(setup.cell, setup.segment(new_node_id(setup.clock), 1)) == 0


async def test_taking_a_cell_never_held_here_still_marks_it_owned() -> None:
    setup = _Setup()

    taken = await setup.segments.take(setup.cell)

    assert taken.events == () and taken.node_ids == frozenset()
    assert setup.segments.veiling(setup.event("cell.destroyed", setup.cell)) == Veiling(
        cell_id=setup.cell, held=False
    )


def test_bind_moves_a_task_to_the_cell_it_runs_on_now_but_file_never_refiles() -> None:
    setup = _Setup()
    first, second = setup.cell, new_cell_id(setup.clock)
    setup.segments.open(first)
    setup.segments.bind(_TASK, first)
    setup.segments.open(second)

    setup.segments.file(_TASK, second)
    assert setup.segments.veiling(setup.event("queen.assigned", _TASK)) == Veiling(first, True)

    setup.segments.bind(_TASK, second)
    assert setup.segments.veiling(setup.event("queen.assigned", _TASK)) == Veiling(second, True)


def test_nothing_is_filed_under_a_cell_this_store_does_not_own() -> None:
    setup = _Setup()

    setup.segments.file(_WARDEN, setup.cell)
    setup.segments.bind(_TASK, setup.cell)

    assert setup.segments.veiling(setup.event("warden.spawned", _WARDEN)) is None
    assert setup.segments.veiling(setup.event("queen.assigned", _TASK)) is None
    assert setup.segments.held_cells() == ()
