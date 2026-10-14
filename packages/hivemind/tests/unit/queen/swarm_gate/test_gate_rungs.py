"""Tests for hivemind.queen.swarm_gate.gate: the Queen's own rung decisions, running in the loop.

The unit tests of the rungs package drive one pass at a time; this one runs the gate itself
(`rig.running()`), moves its clock by hand, and watches a whole life of a device's rung from the
registry: a task that must survive a lost link appears and the loop colonizes the device for it; the
task ends and the loop leaves the rung alone until the reason has been gone for the configured
time, and takes it back then, once the device is idle; and the operator's word wins at every step,
a demotion pinning the device down although the reason still holds.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/gate.py (codingrules section 3), split by feature (14.2)
    from test_gate_ladder.py. The packet and the device's Warden are scripted; the passes are the
    gate's own, woken by the hub.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.gate for the module under test.
    - hivemind.queen.swarm_gate.rungs for the decisions the loop makes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from builders.quarantine import settle
from builders.swarm import (
    INTERVAL_S,
    DialledWarden,
    GateRig,
    ScriptedPacket,
    dial_warden,
    report_state,
    survival_task,
)

from hivemind.brood_chamber import Task
from hivemind.queen.swarm_gate import DeviceWardenDoor
from hivemind.swarm import RuntimeLevel, SwarmNode
from hivemind.swarm.operator import OperatorDesk
from waggle.messages.supervision import WardenState
from waggle.messages.swarm import Colonize, ColonizeAction

_ROUNDS = 40_000  # Scheduling turns a colonization or a withdrawal may take in the running gate.


@dataclass
class _Colonized:
    """A device the running gate colonized for a task pinned to it that must outlive its link."""

    node: SwarmNode
    packet: ScriptedPacket
    pinned: Task
    dialled: list[DialledWarden] = field(default_factory=list)
    judged: int = 0  # How many times the loop has judged a device by its own decisions.

    async def close(self) -> None:
        """End the scripted packet and every Warden that dialled."""
        await self.packet.close()
        for warden in self.dialled:
            await warden.close()


async def _quiet() -> None:
    """Let the gate's loop finish whatever it has in hand."""
    for _ in range(100):
        await asyncio.sleep(0)


async def _level(rig: GateRig, node: SwarmNode) -> RuntimeLevel:
    """The rung the registry holds for `node` now."""
    row = await rig.hive.registry.node(node.device_id)
    assert row is not None
    return row.level


async def _reaches(rig: GateRig, node: SwarmNode, level: RuntimeLevel) -> bool:
    """Let the loop run until the registry holds `node` on `level`; whether it ever did."""
    for _ in range(_ROUNDS):
        if await _level(rig, node) is level:
            return True
        await asyncio.sleep(0)
    return False


def _door_on(rig: GateRig) -> DeviceWardenDoor:
    """The door the gate bound on the listener's router, which is what a device's Warden meets."""
    handler = rig.listener._router._warden_handler
    assert isinstance(handler, DeviceWardenDoor)
    return handler


async def _pass(rig: GateRig, scene: _Colonized) -> None:
    """Wake the loop and wait until it has judged the device once more, and any flow has ended."""
    seen = scene.judged
    await rig.hive.hub.announce_online(scene.node, rig.devices[scene.node.device_id].link)
    await settle(lambda: scene.judged > seen, _ROUNDS)
    await _quiet()  # A flow begins inside the judgement; give it the turns to take the device.
    await settle(lambda: not rig.gate._colonizer.busy(scene.node.device_id), _ROUNDS)
    await _quiet()


async def _colonized_for_a_task(rig: GateRig) -> _Colonized:
    """Run the gate until it has colonized a device for a task pinned to it that must survive."""
    node = await rig.enrol()
    device = await rig.connect(node)
    packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
    pinned = await survival_task(rig.queen_deps, pinned_to=node.cell_id)
    scene = _Colonized(node, packet, pinned)

    async def dial(_order: Colonize) -> None:
        fresh = await rig.hive.registry.node(node.device_id)
        assert fresh is not None
        scene.dialled.append(await dial_warden(rig, fresh, door=_door_on(rig)))

    async def judged(device_node: SwarmNode) -> bool:
        scene.judged += 1
        return await decide(device_node)

    decide = rig.gate._rungs.evaluate
    rig.gate._rungs.evaluate = judged  # type: ignore[assignment]  # Counts, then decides.
    packet.on_start = dial
    await rig.hive.hub.announce_online(node, device.link)  # The loop wakes and serves its Warden.
    await settle(lambda: rig.wardens.get(node.device_id) is not None, _ROUNDS)
    await _pass(rig, scene)  # And on the next pass judges it.
    assert await _reaches(rig, node, RuntimeLevel.COLONIZED)
    return scene


async def test_the_running_gate_colonizes_a_device_for_a_task_that_must_outlive_its_link(
    rig: GateRig,
) -> None:
    async with rig.running():
        await _quiet()
        scene = await _colonized_for_a_task(rig)

        assert scene.packet.actions == [ColonizeAction.PREPARE, ColonizeAction.START]
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "outage" in str(promoted.payload["reason"])  # Hers: nobody asked.
        assert [link.warden_id for link in rig.queen.wardens] == [scene.node.warden_id]
        await scene.close()


async def test_the_running_gate_takes_the_rung_back_when_the_reason_has_been_gone_the_whole_wait(
    rig: GateRig,
) -> None:
    async with rig.running():
        await _quiet()
        scene = await _colonized_for_a_task(rig)
        await rig.queen_deps.chamber.cancel(scene.pinned.id, "The goal ended.")
        report_state(rig, scene.node, WardenState.WATCH)  # Idle, and nothing asks for the rung.
        wait = rig.gate.terms.lower_after_s

        rig.clock.advance(wait / 2)
        await _pass(rig, scene)
        assert await _level(rig, scene.node) is RuntimeLevel.COLONIZED  # Half the wait is not it.
        assert ColonizeAction.WITHDRAW not in scene.packet.actions

        rig.clock.advance(wait / 2)
        await _pass(rig, scene)

        assert await _reaches(rig, scene.node, RuntimeLevel.GATEWAY)
        [demoted] = await rig.hive.events("swarm.demoted")
        assert "reasons_gone" in str(demoted.payload["reason"])
        await scene.close()


async def test_the_running_gate_keeps_the_rung_while_the_device_is_at_work(rig: GateRig) -> None:
    async with rig.running():
        await _quiet()
        scene = await _colonized_for_a_task(rig)
        await rig.queen_deps.chamber.cancel(scene.pinned.id, "The goal ended.")
        report_state(rig, scene.node, WardenState.ACTIVE)  # Its Warden is working.
        rig.clock.advance(10 * rig.gate.terms.lower_after_s)

        await _pass(rig, scene)

        assert await _level(rig, scene.node) is RuntimeLevel.COLONIZED
        assert ColonizeAction.WITHDRAW not in scene.packet.actions
        report_state(rig, scene.node, WardenState.WATCH)  # It stops; the wait was long over.
        await _pass(rig, scene)
        assert await _reaches(rig, scene.node, RuntimeLevel.GATEWAY)
        await scene.close()


async def test_the_operators_demotion_wins_although_the_reason_for_the_rung_still_holds(
    rig: GateRig,
) -> None:
    async with rig.running():
        await _quiet()
        scene = await _colonized_for_a_task(rig)  # The pinned task is still there.
        report_state(rig, scene.node, WardenState.WATCH)
        row = await rig.hive.registry.node(scene.node.device_id)
        assert row is not None
        await OperatorDesk(rig.hive.registry, rig.hive.recorder).move(row, promote=False)

        await _pass(rig, scene)

        assert await _reaches(rig, scene.node, RuntimeLevel.GATEWAY)
        for _ in range(3):  # And she does not take it back up, however many passes go by.
            rig.clock.advance(INTERVAL_S)  # Short of the time a silent device is given up on.
            await _pass(rig, scene)
        assert await _level(rig, scene.node) is RuntimeLevel.GATEWAY
        assert scene.packet.actions.count(ColonizeAction.PREPARE) == 1
        await scene.close()
