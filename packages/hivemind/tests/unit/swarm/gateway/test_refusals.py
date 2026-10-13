"""Tests for hivemind.swarm.gateway.refusals: what the transport limits refused, on record.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/refusals.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The hub and the trail are the real in-memory ones; refusals are built by hand, so
    each rule (the always-recorded flood, the silent reconnection duplicate) is tested alone.
    The same refusals arriving from a real socket are in test_listener.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.refusals for the module under test.
    - docs/adr/0055-waggle-replay-window-and-frame-ceiling.md for the grace rule.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import structlog.testing
from builders.cells import make_identity
from builders.swarm import RawLink, make_raw_link, make_swarm_node

from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway import GatewayHub, TransportRefusals
from hivemind.swarm.gateway.refusals import (
    FRAME_CEILING_KIND,
    RECONNECT_GRACE_S,
    REPLAY_REFUSED_KIND,
)
from hivemind.swarm.models import SwarmNode
from waggle.ids import NodeId, new_message_id
from waggle.transport.limits import ConnectionRefusal, RefusalKind


class _DownTrail(MemoryPheromoneTrail):
    """A trail whose store is unavailable."""

    async def record(self, event: PheromoneEvent) -> None:
        """Fail like a database that cannot be written."""
        raise sqlite3.OperationalError("database is locked")


@dataclass
class _Rig:
    raw: RawLink
    hub: GatewayHub
    trail: MemoryPheromoneTrail
    refusals: TransportRefusals
    node: SwarmNode

    def refusal(
        self, kind: RefusalKind, node_id: NodeId | None, *, with_id: bool = True
    ) -> ConnectionRefusal:
        """A refusal of `kind` about `node_id`, stamped now."""
        envelope_id = str(new_message_id(self.raw.clock)) if with_id else None
        return ConnectionRefusal(node_id, kind, envelope_id, self.raw.clock.now())

    async def recorded(self) -> list[PheromoneEvent]:
        """Every event on the trail, oldest first."""
        return list(await self.trail.query(TrailQuery()))


@pytest.fixture
async def rig() -> AsyncIterator[_Rig]:
    """Refusals over a hub with one attached device and an empty trail."""
    raw = await make_raw_link()
    trail = MemoryPheromoneTrail(raw.clock)
    hub = GatewayHub(raw.clock)
    node = make_swarm_node(raw.party, raw.clock)
    await hub.attach(node, raw.link)
    recorder = SwarmRecorder(trail, raw.clock, make_identity(raw.clock, hive_id=raw.hive.hive_id))
    yield _Rig(raw, hub, trail, TransportRefusals(recorder, hub, raw.hive.hive_id), node)
    await raw.close()


async def test_a_flood_is_recorded_against_the_node_that_sent_it(rig: _Rig) -> None:
    await rig.refusals(rig.refusal(RefusalKind.FRAME_CEILING, rig.node.node_id, with_id=False))

    (event,) = await rig.recorded()
    assert event.kind == FRAME_CEILING_KIND
    assert event.subject_id == rig.node.node_id
    assert event.payload == {"node_id": rig.node.node_id, "envelope_id": None}
    assert event.actor == "system"


async def test_a_flood_before_any_frame_decoded_is_recorded_against_the_hive(rig: _Rig) -> None:
    await rig.refusals(rig.refusal(RefusalKind.FRAME_CEILING, None, with_id=False))

    (event,) = await rig.recorded()
    assert event.subject_id == rig.raw.hive.hive_id
    assert event.payload == {"node_id": None, "envelope_id": None}


async def test_a_flood_is_recorded_even_just_after_a_reconnection(rig: _Rig) -> None:
    await rig.refusals(rig.refusal(RefusalKind.FRAME_CEILING, rig.node.node_id, with_id=False))

    assert [event.kind for event in await rig.recorded()] == [FRAME_CEILING_KIND]


async def test_a_repeated_id_long_after_the_link_attached_is_recorded(rig: _Rig) -> None:
    rig.raw.clock.advance(RECONNECT_GRACE_S + 1.0)
    refusal = rig.refusal(RefusalKind.REPLAY, rig.node.node_id)

    await rig.refusals(refusal)

    (event,) = await rig.recorded()
    assert event.kind == REPLAY_REFUSED_KIND
    assert event.subject_id == rig.node.node_id
    assert event.payload == {"node_id": rig.node.node_id, "envelope_id": refusal.envelope_id}


@pytest.mark.parametrize("after_s", [0.5, 30.0, RECONNECT_GRACE_S])
async def test_a_repeated_id_within_the_reconnection_grace_is_dropped_silently(
    rig: _Rig, after_s: float
) -> None:
    rig.raw.clock.advance(after_s)

    await rig.refusals(rig.refusal(RefusalKind.REPLAY, rig.node.node_id))

    assert await rig.recorded() == []


async def test_a_repeated_id_from_a_node_that_never_attached_is_recorded(rig: _Rig) -> None:
    stranger = make_swarm_node(clock=rig.raw.clock)

    await rig.refusals(rig.refusal(RefusalKind.REPLAY, stranger.node_id))

    (event,) = await rig.recorded()
    assert event.kind == REPLAY_REFUSED_KIND
    assert event.subject_id == stranger.node_id


async def test_a_repeated_id_from_no_known_node_is_recorded_against_the_hive(rig: _Rig) -> None:
    await rig.refusals(rig.refusal(RefusalKind.REPLAY, None))

    (event,) = await rig.recorded()
    assert event.subject_id == rig.raw.hive.hive_id


async def test_the_grace_is_the_one_the_listener_is_configured_with(rig: _Rig) -> None:
    recorder = SwarmRecorder(
        rig.trail, rig.raw.clock, make_identity(rig.raw.clock, hive_id=rig.raw.hive.hive_id)
    )
    tight = TransportRefusals(recorder, rig.hub, rig.raw.hive.hive_id, grace_s=1.0)
    rig.raw.clock.advance(2.0)

    await tight(rig.refusal(RefusalKind.REPLAY, rig.node.node_id))

    assert [event.kind for event in await rig.recorded()] == [REPLAY_REFUSED_KIND]


async def test_a_trail_that_is_down_does_not_turn_a_refusal_into_a_crash() -> None:
    raw = await make_raw_link()
    try:
        recorder = SwarmRecorder(
            _DownTrail(raw.clock),
            raw.clock,
            make_identity(raw.clock, hive_id=raw.hive.hive_id),
        )
        refusals = TransportRefusals(recorder, GatewayHub(raw.clock), raw.hive.hive_id)
        flood = ConnectionRefusal(None, RefusalKind.FRAME_CEILING, None, raw.clock.now())

        with structlog.testing.capture_logs() as captured:
            await refusals(flood)

        [failed] = [entry for entry in captured if entry["event"] == "swarm.refusal_not_recorded"]
        assert failed["log_level"] == "error"
        assert failed["error"] == "OperationalError" and failed["kind"] == FRAME_CEILING_KIND
    finally:
        await raw.close()
