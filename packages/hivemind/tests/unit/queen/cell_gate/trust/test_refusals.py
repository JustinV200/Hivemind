"""Tests for hivemind.queen.cell_gate.trust.refusals: the listener's transport limits and record.

The listener's two transport defences (roadmap steps 11.3a and 11.3b, ADR-0055) are recorded as
`guard.frame_ceiling` and `guard.replay_refused` with the node's id, and with no recorder the limits
still apply but nothing is written. A duplicate envelope id shortly after a Cell's Warden
reconnected is its outbox replaying and is dropped silently (the hub remembers the reconnection),
while the same duplicate with no reconnection noted is the refusal it looks like. The per-link
refusals live with the attach path and are tested in tests/unit/queen/attach/test_refusals.py.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/trust/refusals.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.unit.queen.attach.test_refusals for the per-link refusals this module re-exports.
    - tests.unit.queen.cell_gate.test_listener for the same refusals over a real socket.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.pheromone import TrailQuery, TrailRecorder
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate import transport_options, transport_refusals
from hivemind.swarm.gateway import GatewayHub
from waggle.clock import FakeClock
from waggle.ids import HiveId, NodeId, new_event_id, new_hive_id, new_node_id
from waggle.transport.limits import ConnectionRefusal, RefusalHandler, RefusalKind


@dataclass(frozen=True, slots=True)
class _Queen:
    """The Queen's trail and identity, and the node a refusal is about."""

    clock: FakeClock
    hive_id: HiveId
    node_id: NodeId
    trail: MemoryPheromoneTrail
    recorder: TrailRecorder

    def handler(self, attached: GatewayHub | None = None) -> RefusalHandler:
        """The refusal callback this listener's server runs with."""
        handler = transport_refusals(self.recorder, attached)
        assert handler is not None
        return handler


def _queen() -> _Queen:
    """A fresh Queen trail with a recorder, and a node some refusal is about."""
    clock = FakeClock()
    hive_id, node_id = new_hive_id(clock), new_node_id(clock)
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailRecorder(trail, clock, hive_id, new_node_id(clock))
    return _Queen(clock, hive_id, node_id, trail, recorder)


def _refusal(clock: FakeClock, kind: RefusalKind, node_id: str | None) -> ConnectionRefusal:
    """One refusal as waggle's transport hands it over: a replayed id, or a flood."""
    envelope_id = str(new_event_id(clock)) if kind is RefusalKind.REPLAY else None
    return ConnectionRefusal(node_id=node_id, kind=kind, envelope_id=envelope_id, at=clock.now())


async def test_a_replayed_envelope_id_is_recorded_as_guard_replay_refused_naming_the_node() -> None:
    queen = _queen()
    refusal = _refusal(queen.clock, RefusalKind.REPLAY, queen.node_id)

    await queen.handler()(refusal)

    [event] = await queen.trail.query(TrailQuery(kind="guard.replay_refused"))
    assert event.subject_id == queen.node_id
    assert event.payload == {"node_id": queen.node_id, "envelope_id": refusal.envelope_id}
    assert event.node_id == queen.recorder.node_id  # The Queen's own record, never the Cell's.


async def test_a_frame_flood_is_recorded_as_guard_frame_ceiling_naming_the_node() -> None:
    queen = _queen()

    await queen.handler()(_refusal(queen.clock, RefusalKind.FRAME_CEILING, queen.node_id))

    [event] = await queen.trail.query(TrailQuery(kind="guard.frame_ceiling"))
    assert event.subject_id == queen.node_id
    assert event.payload == {"node_id": queen.node_id, "envelope_id": None}


async def test_a_flood_before_any_node_was_named_is_recorded_against_the_hive() -> None:
    queen = _queen()

    await queen.handler()(_refusal(queen.clock, RefusalKind.FRAME_CEILING, None))

    [event] = await queen.trail.query(TrailQuery(kind="guard.frame_ceiling"))
    assert event.subject_id == queen.hive_id


async def test_a_duplicate_right_after_the_nodes_warden_reconnected_is_silent() -> None:
    queen = _queen()
    hub = GatewayHub(queen.clock)
    hub.note_attach(queen.node_id)  # Its Warden just dialled again: the outbox is replaying.

    await queen.handler(hub)(_refusal(queen.clock, RefusalKind.REPLAY, queen.node_id))

    assert await queen.trail.query(TrailQuery(kind="guard.replay_refused")) == ()


async def test_a_duplicate_long_after_the_reconnection_is_the_refusal_it_looks_like() -> None:
    queen = _queen()
    hub = GatewayHub(queen.clock)
    hub.note_attach(queen.node_id)
    queen.clock.advance(3600.0)  # Far outside any outbox replay.

    await queen.handler(hub)(_refusal(queen.clock, RefusalKind.REPLAY, queen.node_id))

    assert len(await queen.trail.query(TrailQuery(kind="guard.replay_refused"))) == 1


async def test_a_duplicate_from_a_node_that_never_reconnected_is_recorded() -> None:
    queen = _queen()
    hub = GatewayHub(queen.clock)
    hub.note_attach(new_node_id(queen.clock))  # Some other node's Warden reconnected.

    await queen.handler(hub)(_refusal(queen.clock, RefusalKind.REPLAY, queen.node_id))

    assert len(await queen.trail.query(TrailQuery(kind="guard.replay_refused"))) == 1


def test_without_a_recorder_the_listener_records_nothing_but_still_applies_the_limits() -> None:
    assert transport_refusals(None) is None

    options = transport_options(None, FakeClock())

    assert options["limits"] is not None  # Both limits are on, at waggle's defaults.
    assert options["on_refusal"] is None
