"""Tests for hivemind.queen.swarm_gate.colonize.withdraw: an idle Warden going back down.

The operator asks for a lower rung; the Queen honours it only while the device's own Warden is
idle (it reports WATCH and holds no task), since its Handoffs live on the device. The flow is run
against a scripted packet, with the device's Warden attached to a real Queen as it would be after a
colonization. What is asserted is what the Queen does and leaves behind: the packet's order, the
rung, the trail, the Warden's link, and the Level 0 Warden rebuilt in its place.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/withdraw.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.withdraw for the module under test.
    - hivemind.queen.swarm_gate.colonize.request for when a withdrawal is refused.
"""

from __future__ import annotations

from builders.isolation import place_running
from builders.quarantine import settle
from builders.swarm import (
    GateRig,
    ScriptedPacket,
    attach_device_warden,
    report_state,
)

from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import WardenLink
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.messages.supervision import WardenState
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.


async def _colonized(rig: GateRig) -> tuple[SwarmNode, WardenLink, ScriptedPacket]:
    """A device on Level 1 with its own Warden attached and idle, asking for Level 0."""
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    device = await rig.connect(node)
    packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
    link, _end = await attach_device_warden(rig, node)
    report_state(rig, node, WardenState.WATCH)
    await rig.hive.registry.set_desired_level(node.device_id, RuntimeLevel.GATEWAY)
    return node, link, packet


async def _ask(rig: GateRig, node: SwarmNode) -> bool:
    """Ask the colonizer to carry out the device's pending request; whether a flow began."""
    row = await rig.hive.registry.node(node.device_id)
    assert row is not None
    return await rig.colonizer.request(row)


async def _finished(rig: GateRig, node: SwarmNode) -> None:
    """Wait until no flow owns the device any more."""
    await settle(lambda: not rig.colonizer.busy(node.device_id), _FLOW_ROUNDS)


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def test_an_idle_warden_is_withdrawn_and_the_hive_stand_serves_the_device_again(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node, link, packet = await _colonized(rig)

        assert await _ask(rig, node)
        await _finished(rig, node)

        row = await rig.hive.registry.node(node.device_id)
        assert row is not None and row.level is RuntimeLevel.GATEWAY and row.desired_level is None
        assert packet.actions == [ColonizeAction.WITHDRAW]
        [demoted] = await rig.hive.events("swarm.demoted")
        assert (demoted.payload["from_level"], demoted.payload["to_level"]) == (
            "COLONIZED",
            "GATEWAY",
        )
        assert not link.transport.is_connected  # The device's Warden was let go.
        assert rig.wardens.get(node.device_id) is not None  # And a Level 0 Warden takes over.
        assert [wl.node_id for wl in rig.queen.wardens] == [None]
        recorded = rig.hive.hub.node(node.device_id)
        assert recorded is not None and recorded.level is RuntimeLevel.GATEWAY
        await settle(lambda: bool(packet.updates))
        assert packet.updates[-1].level is RuntimeLevel.GATEWAY  # The packet knows its rung.
        await packet.close()


async def test_a_warden_that_reports_it_is_working_is_refused_with_a_reason(rig: GateRig) -> None:
    async with rig.serving():
        node, _link, packet = await _colonized(rig)
        report_state(rig, node, WardenState.ACTIVE)

        assert not await _ask(rig, node)

        assert packet.orders == []  # The device was not asked anything.
        row = await rig.hive.registry.node(node.device_id)
        assert row is not None and row.level is RuntimeLevel.COLONIZED
        assert row.desired_level is None  # Answered, so it is not asked again on every pass.
        assert ("rung_refused", "warden_busy") in await _decisions(rig)
        await packet.close()


async def test_a_warden_that_holds_a_task_is_refused_even_if_it_says_watch(rig: GateRig) -> None:
    async with rig.serving():
        node, link, packet = await _colonized(rig)
        await place_running(rig.queen_deps, link)

        assert not await _ask(rig, node)

        assert ("rung_refused", "warden_busy") in await _decisions(rig)
        await packet.close()


async def test_a_warden_that_has_not_reported_yet_is_not_idle(rig: GateRig) -> None:
    async with rig.serving():
        node, _link, packet = await _colonized(rig)
        rig.queen._last_heartbeat.pop(node.warden_id)

        assert not await _ask(rig, node)

        assert ("rung_refused", "warden_busy") in await _decisions(rig)
        await packet.close()


async def test_a_packet_that_cannot_stop_the_warden_leaves_everything_as_it_was(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node, link, packet = await _colonized(rig)
        packet.outcomes[ColonizeAction.WITHDRAW] = ColonizeOutcome.FAILED

        assert await _ask(rig, node)
        await _finished(rig, node)

        row = await rig.hive.registry.node(node.device_id)
        assert row is not None and row.level is RuntimeLevel.COLONIZED  # Still on Level 1.
        assert row.desired_level is None
        assert link.transport.is_connected and rig.queen.wardens == (link,)
        assert rig.wardens.get(node.device_id) is None
        assert await rig.hive.events("swarm.demoted") == []
        assert ("rung_failed", "withdraw_failed") in await _decisions(rig)
        await packet.close()
