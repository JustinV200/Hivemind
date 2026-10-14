"""Tests for hivemind.queen.swarm_gate.gate: the runtime ladder, the door and the hold, running.

The gate's loop is what turns a registry row into a flow, and the listener's router is what hands
a device's own Warden to the gate's door. These tests run the gate itself (`rig.running()`), bind
nothing by hand, and watch the whole chain: an operator's request is found by the loop and
colonized, the door the gate bound on the listener admits the Warden the flow started, a lost
device is held and a diagnosis is asked for, and stopping the gate ends a flow still running.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/gate.py (codingrules section 3), split by feature
    (14.2) from test_gate.py. The packet and the device's Warden are scripted.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.gate for the module under test.
"""

from __future__ import annotations

import asyncio

from builders.quarantine import settle
from builders.swarm import (
    DialledWarden,
    GateRig,
    ScriptedPacket,
    dial_warden,
    place_on_device,
)

from hivemind.queen.swarm_gate import DeviceWardenDoor, HoldState, UnreachableDevices
from hivemind.swarm import NodeStatus, RuntimeLevel, SwarmNode
from waggle.messages.swarm import Colonize, ColonizeAction

_ROUNDS = 40_000  # Scheduling turns a colonization may take in the running gate.


class _Diagnosis:
    """A diagnosis that notes each hold it was asked to begin and end."""

    def __init__(self) -> None:
        self.begun: list[str] = []
        self.ended: list[str] = []

    async def begin(self, node: SwarmNode, reason: str) -> None:
        self.begun.append(reason)

    async def end(self, node: SwarmNode) -> None:
        self.ended.append(node.device_id)


async def _quiet() -> None:
    """Let the gate's loop finish whatever it has in hand (its first pass, say)."""
    for _ in range(30):
        await asyncio.sleep(0)


def _door_on(rig: GateRig) -> DeviceWardenDoor:
    """The door the gate bound on the listener's router, which is what a device's Warden meets."""
    handler = rig.listener._router._warden_handler
    assert isinstance(handler, DeviceWardenDoor)
    return handler


async def _level(rig: GateRig, node: SwarmNode) -> RuntimeLevel:
    """The rung the registry holds for `node` now."""
    row = await rig.hive.registry.node(node.device_id)
    assert row is not None
    return row.level


async def test_start_binds_the_door_a_colonized_devices_warden_will_dial(rig: GateRig) -> None:
    await rig.gate.start(rig.queen, rig.queen_deps)

    assert isinstance(_door_on(rig), DeviceWardenDoor)  # Bound before the listener took a dial.


async def test_the_running_gate_colonizes_a_device_the_operator_asked_to_promote(
    rig: GateRig,
) -> None:
    async with rig.running():
        await _quiet()
        node = await rig.enrol()
        device = await rig.connect(node)
        packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
        dialled: list[DialledWarden] = []

        async def dial(_order: Colonize) -> None:
            fresh = await rig.hive.registry.node(node.device_id)
            assert fresh is not None
            dialled.append(await dial_warden(rig, fresh, door=_door_on(rig)))

        packet.on_start = dial
        await rig.hive.registry.set_desired_level(node.device_id, RuntimeLevel.COLONIZED)

        await rig.hive.hub.announce_online(node, device.link)  # The loop wakes and looks.
        for _ in range(_ROUNDS):
            if await _level(rig, node) is RuntimeLevel.COLONIZED:
                break
            await asyncio.sleep(0)

        assert await _level(rig, node) is RuntimeLevel.COLONIZED
        assert packet.actions == [ColonizeAction.PREPARE, ColonizeAction.START]
        assert [link.warden_id for link in rig.queen.wardens] == [node.warden_id]
        assert rig.queen.wardens[0].node_id == node.node_id  # Its Warden is the device's own.
        await packet.close()
        for warden in dialled:
            await warden.close()


async def test_a_lost_device_has_its_tasks_held_and_a_diagnosis_asked_for(rig: GateRig) -> None:
    diagnosis = _Diagnosis()
    rig.gate.use_diagnosis(diagnosis)
    async with rig.running():
        await _quiet()
        placed = await place_on_device(rig)
        await settle(lambda: rig.wardens.get(placed.node.device_id) is not None)
        await rig.disconnect(placed.node)
        silent = await rig.hive.registry.set_status(
            placed.node.device_id, NodeStatus.ONLINE, NodeStatus.UNREACHABLE, rig.clock.now()
        )

        await rig.hive.hub.announce_offline(silent, "closed")
        await settle(lambda: bool(diagnosis.begun), _ROUNDS)

        assert diagnosis.begun == ["closed"]  # The reason the hub gave, nothing else.
        assert rig.gate.unreachable.hold_of(placed.node.device_id) is HoldState.HELD
        assert rig.queen_deps.dispatch.held.is_held(placed.node.cell_id)


async def test_the_gate_exposes_the_hold_on_lost_devices(rig: GateRig) -> None:
    assert isinstance(rig.gate.unreachable, UnreachableDevices)


async def test_stopping_the_gate_ends_a_colonization_still_waiting_on_its_packet(
    rig: GateRig,
) -> None:
    async with rig.running():
        await _quiet()
        node = await rig.enrol()
        device = await rig.connect(node)
        packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
        packet.silent.add(ColonizeAction.PREPARE)  # The install never finishes.
        await rig.hive.registry.set_desired_level(node.device_id, RuntimeLevel.COLONIZED)
        await rig.hive.hub.announce_online(node, device.link)
        await settle(lambda: bool(packet.orders), _ROUNDS)

    # Leaving the block stopped the gate and awaited its run: a flow left running would hang it.
    assert not rig.gate._colonizer.busy(node.device_id)
    await packet.close()
