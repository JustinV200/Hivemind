"""Tests for hivemind.queen.swarm_gate.attach: the door a colonized device's own Warden dials.

The door is the gate's policy for a verified `cell.ready` connection the swarm listener hands it,
and the shared attach path does the rest. A Warden dials with a memory pair and the first frame a
real one sends; what is asserted is who is let in, what the Queen then holds, and what the gate is
told when the Warden attaches and when its connection ends.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/attach.py (codingrules section 3). The Queen, registry,
    hub and Guard are the real classes; the Warden's end is a scripted `WardenEnd`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.attach for the module under test.
    - tests.unit.queen.attach.test_serve for the shared path the door hands connections to.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from builders.forage import make_capacity
from builders.quarantine import settle
from builders.queen import with_guard_policy
from builders.segments import SegmentRig, make_segment_rig, segment_chunks, signed
from builders.swarm import (
    GateRig,
    capacity_report_of,
    dial_warden,
    enrol_node,
    make_device_party,
    make_swarm_node,
)

from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate import DeviceWardenDoor, GateContext, link_services
from hivemind.queen.swarm_gate.attach import through_the_gate
from hivemind.queen.trail import TrailSegmentReceiver
from hivemind.swarm import NodeStatus, RuntimeLevel, SwarmNode
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectionLostError
from waggle.ids import new_cell_id, new_message_id, new_warden_id
from waggle.messages.cell.status import CellReady
from waggle.messages.swarm import NucAction, NucOutcome, NucPromoted
from waggle.signing import Ed25519Signer

_ROUNDS = 5_000  # Scheduling turns to wait for the Queen to hear what a Warden sent.


class _Events:
    """A gate that admits as told, and notes each attach and each end."""

    def __init__(self, *, admits: bool = True) -> None:
        self.admit = admits
        self.attached_with: list[bool] = []
        self.ended_links: list[WardenLink] = []
        self.frames: list[Envelope] = []  # Every frame the door showed the gate, in order.
        self.consumes: Callable[[Envelope], bool] = lambda envelope: False

    def admits(self, node: SwarmNode) -> bool:
        return self.admit

    async def attached(self, node: SwarmNode, link: WardenLink, reconnected: bool) -> None:
        self.attached_with.append(reconnected)

    async def ended(self, node: SwarmNode, link: WardenLink) -> None:
        self.ended_links.append(link)

    def heard(self, node: SwarmNode, envelope: Envelope) -> bool:
        self.frames.append(envelope)
        return self.consumes(envelope)


def _door(rig: GateRig, events: _Events, queen: Queen | None = None) -> DeviceWardenDoor:
    """A door over the rig's listener and registry, for `queen` (the rig's own by default)."""
    context = GateContext(queen if queen is not None else rig.queen, rig.queen_deps)
    return DeviceWardenDoor(rig.listener, rig.hive.registry, context, events)


async def test_a_colonized_devices_warden_is_attached_as_a_remote_warden_of_its_cell(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()

    dialled = await dial_warden(rig, node, door=_door(rig, events))
    await settle(lambda: len(rig.queen.wardens) == 1)

    [link] = rig.queen.wardens
    assert link.warden_id == node.warden_id and link.node_id == node.node_id
    assert link.cell == node.as_cell()  # The Cell the registry holds: its tier and access level.
    voice = rig.hive.voice
    assert (link.hop.sender, link.hop.recipient) == (voice.hive_id, node.warden_id)
    assert link.hop.node_id == voice.hive_node_id
    [spawned] = await rig.queen_deps.trail.query(TrailQuery(kind="warden.spawned"))
    assert spawned.payload["node_id"] == node.node_id
    assert events.attached_with == [False]  # New to the Queen.
    await dialled.close()


async def test_the_devices_capacity_is_read_from_the_hub_as_the_packet_last_reported_it(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    dialled = await dial_warden(rig, node, door=_door(rig, _Events()))
    await settle(lambda: len(rig.queen.wardens) == 1)
    [link] = rig.queen.wardens
    assert link.live_capacity is not None
    assert await link.live_capacity() == node.capacity  # Nothing newer on the hub: its own.

    newer = node.model_copy(update={"capacity": make_capacity(max_sub_bees=1)})
    await rig.connect(newer)

    assert (await link.live_capacity()).max_sub_bees == 1
    await dialled.close()


async def test_a_level0_devices_warden_is_not_admitted_unless_the_gate_wants_one(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.GATEWAY)  # Its Warden is on the Hive Stand.
    events = _Events(admits=False)

    dialled = await dial_warden(rig, node, door=_door(rig, events))
    await asyncio.wait_for(dialled.handler, timeout=5.0)

    assert rig.queen.wardens == ()
    assert events.attached_with == []


async def test_a_level0_devices_warden_the_gate_started_is_admitted(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.GATEWAY)
    events = _Events(admits=True)  # A colonization is waiting for it.

    dialled = await dial_warden(rig, node, door=_door(rig, events))
    await settle(lambda: len(rig.queen.wardens) == 1)

    assert events.attached_with == [False]
    await dialled.close()


async def test_the_gate_is_shown_the_handshake_frame_once_and_then_the_rest(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()
    dialled = await dial_warden(rig, node, door=_door(rig, events))
    await settle(lambda: len(rig.queen.wardens) == 1)
    hop = Hop(sender=node.warden_id, recipient=rig.hive.voice.hive_id, node_id=node.node_id)
    report = wrap(capacity_report_of(node, "box"), hop, clock=rig.clock)

    await dialled.transport.send(report)
    await settle(lambda: len(events.frames) == 2)

    # The handshake's `cell.ready` is not in the frames the Queen's reader is given, so the door
    # shows it itself; without it the gate could not know the terms the Warden was started under.
    first, second = events.frames
    assert isinstance(first.payload, CellReady) and first.sender == node.warden_id
    assert second == report
    await dialled.close()


async def test_a_warden_naming_another_cell_is_not_admitted_nor_shown_to_the_gate(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()

    dialled = await dial_warden(rig, node, door=_door(rig, events), cell_id=new_cell_id(rig.clock))
    await asyncio.wait_for(dialled.handler, timeout=5.0)

    assert rig.queen.wardens == ()
    assert events.frames == []  # A frame that fails the handshake says nothing the gate may note.


async def test_a_warden_that_is_not_the_nodes_own_is_not_admitted(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)

    other = new_warden_id(rig.clock)
    dialled = await dial_warden(rig, node, door=_door(rig, _Events()), sender=other)
    await asyncio.wait_for(dialled.handler, timeout=5.0)

    assert rig.queen.wardens == ()


async def test_a_revoked_devices_warden_is_not_admitted(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    event = rig.hive.recorder.swarm_event("swarm.revoked", node.device_id, {})
    revoked = await rig.hive.registry.set_status(
        node.device_id, node.status, NodeStatus.REVOKED, rig.clock.now(), event
    )

    dialled = await dial_warden(rig, revoked, door=_door(rig, _Events()))
    await asyncio.wait_for(dialled.handler, timeout=5.0)

    assert rig.queen.wardens == ()


async def test_a_warden_the_guard_refuses_is_never_attached(rig: GateRig) -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("forage:request",))})
    deps = with_guard_policy(rig.queen_deps, load_guard_policy(None, section))
    refusing = Queen(deps)
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()
    door = DeviceWardenDoor(rig.listener, rig.hive.registry, GateContext(refusing, deps), events)

    dialled = await dial_warden(rig, node, door=door)
    await asyncio.wait_for(dialled.handler, timeout=5.0)  # Returns: the listener closes it.

    assert refusing.wardens == () and events.attached_with == []
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["capability"] == "warden:spawn"


async def test_a_warden_that_comes_back_is_a_reconnection_not_a_new_warden(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()
    door = _door(rig, events)
    first = await dial_warden(rig, node, door=door)
    await settle(lambda: len(rig.queen.wardens) == 1)
    await first.close()
    await settle(lambda: len(events.ended_links) == 1)

    second = await dial_warden(rig, node, door=door)
    await settle(lambda: len(events.attached_with) == 2)

    assert events.attached_with == [False, True]
    assert len(await rig.queen_deps.trail.query(TrailQuery(kind="warden.spawned"))) == 1
    assert len(await rig.queen_deps.trail.query(TrailQuery(kind="warden.reconnected"))) == 1
    await second.close()


async def test_when_the_connection_ends_the_gate_is_told_and_the_queen_keeps_the_entry(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()
    dialled = await dial_warden(rig, node, door=_door(rig, events))
    await settle(lambda: len(rig.queen.wardens) == 1)
    [link] = rig.queen.wardens

    await dialled.close()  # The device went away.
    await settle(lambda: len(events.ended_links) == 1)

    assert events.ended_links == [link]
    # A device is somebody's machine and comes back: its entry stays for the hold to use.
    assert rig.queen.wardens == (link,)
    assert not link.transport.is_connected


async def test_a_connection_that_ends_after_a_newer_one_attached_is_not_reported(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()
    door = _door(rig, events)
    old = await dial_warden(rig, node, door=door)
    await settle(lambda: len(events.attached_with) == 1)
    new = await dial_warden(rig, node, door=door)  # It reconnected before the old one was noticed.
    await settle(lambda: len(events.attached_with) == 2)

    await old.close()
    await asyncio.wait_for(old.handler, timeout=5.0)

    assert events.ended_links == []  # The Warden is attached under the new link: nothing to hold.
    assert len(rig.queen.wardens) == 1 and rig.queen.wardens[0].transport.is_connected
    await new.close()


async def _enrolled_with_key(rig: GateRig) -> tuple[SwarmNode, SegmentRig]:
    """Enrol a colonized device whose key is known, and the segment ids and key it signs with."""
    clock = rig.clock
    party = make_device_party(clock)
    node = await enrol_node(
        rig.hive.registry, make_swarm_node(party, clock, level=RuntimeLevel.COLONIZED), clock
    )
    base = make_segment_rig(clock)
    segment = SegmentRig(
        clock=clock,
        hive_id=base.hive_id,
        cell_id=party.cell_id,
        node_id=party.node_id,
        warden_id=party.warden_id,
        signer=party.signer,
    )
    return node, segment


async def _until(rig: GateRig, kind: str) -> None:
    """Wait until an event of `kind` is on the Queen's trail."""
    for _ in range(_ROUNDS):
        if await rig.queen_deps.trail.query(TrailQuery(kind=kind)):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"No {kind} event reached the Queen's trail.")


async def test_a_segment_signed_with_the_devices_key_is_merged_into_the_queens_trail(
    rig: GateRig,
) -> None:
    node, segment = await _enrolled_with_key(rig)
    dialled = await dial_warden(rig, node, door=_door(rig, _Events()))
    await settle(lambda: len(rig.queen.wardens) == 1)

    for chunk in signed(await segment_chunks(segment), segment.signer):
        await dialled.end.send(chunk)

    await _until(rig, "cell.provisioned")
    assert await rig.queen_deps.trail.query(TrailQuery(kind="guard.segment_refused")) == ()
    await dialled.close()


async def test_an_unsigned_segment_from_a_device_is_refused_and_recorded(rig: GateRig) -> None:
    node, segment = await _enrolled_with_key(rig)
    dialled = await dial_warden(rig, node, door=_door(rig, _Events()))
    await settle(lambda: len(rig.queen.wardens) == 1)

    for chunk in await segment_chunks(segment):
        await dialled.end.send(chunk)

    await _until(rig, "guard.segment_refused")
    [refusal] = await rig.queen_deps.trail.query(TrailQuery(kind="guard.segment_refused"))
    assert refusal.payload["reason"] == "unsigned"
    assert refusal.payload["node_id"] == node.node_id
    assert await rig.queen_deps.trail.query(TrailQuery(kind="cell.provisioned")) == ()
    await dialled.close()


async def test_a_segment_signed_with_another_key_is_refused_as_a_bad_signature(
    rig: GateRig,
) -> None:
    node, segment = await _enrolled_with_key(rig)
    dialled = await dial_warden(rig, node, door=_door(rig, _Events()))
    await settle(lambda: len(rig.queen.wardens) == 1)

    for chunk in signed(await segment_chunks(segment), Ed25519Signer.generate()):
        await dialled.end.send(chunk)

    await _until(rig, "guard.segment_refused")
    [refusal] = await rig.queen_deps.trail.query(TrailQuery(kind="guard.segment_refused"))
    assert refusal.payload["reason"] == "bad_signature"
    await dialled.close()


async def test_the_services_require_a_signed_segment_and_answer_no_snapshots(rig: GateRig) -> None:
    services = link_services(GateContext(rig.queen, rig.queen_deps), rig.hive.registry)

    assert services.snapshots is None  # A device is borrowed, never snapshotted.
    assert isinstance(services.trail, TrailSegmentReceiver)
    assert services.recorder is not None
    assert services.recorder.hive_id == rig.queen_deps.identity.hive_id
    assert services.recorder.node_id == rig.queen_deps.identity.node_id


async def _frames_of(*frames: Envelope, then: Exception | None = None) -> AsyncIterator[Envelope]:
    """A connection's frames: these, then the end or the failure that ended it."""
    for frame in frames:
        yield frame
    if then is not None:
        raise then


async def test_the_gate_sees_every_frame_before_the_queen_and_keeps_only_what_is_its_own(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    events = _Events()
    events.consumes = lambda envelope: isinstance(envelope.payload, NucPromoted)
    hop = Hop(sender=node.warden_id, recipient=rig.hive.voice.hive_id, node_id=node.node_id)
    answer = NucPromoted(
        cell_id=node.cell_id,
        device_id=node.device_id,
        action=NucAction.DEMOTE,
        outcome=NucOutcome.DEMOTED,
        level=RuntimeLevel.COLONIZED,
        server_id=None,
        loaded_models=(),
        reason="Scripted.",
    )
    mine = wrap(answer, hop, clock=rig.clock, correlation_id=new_message_id(rig.clock))
    report = wrap(capacity_report_of(node, "box"), hop, clock=rig.clock)

    kept = [f async for f in through_the_gate(_frames_of(mine, report, mine), node, events)]

    assert kept == [report]  # The Queen hears the report, in order, and none of the answers.
    assert events.frames == [mine, report, mine]  # The gate saw every one, first.


async def test_a_connection_that_fails_fails_the_same_way_for_the_queens_reader(
    rig: GateRig,
) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    lost = ConnectionLostError("the link went away")

    with pytest.raises(ConnectionLostError):
        async for _frame in through_the_gate(_frames_of(then=lost), node, _Events()):
            pass
