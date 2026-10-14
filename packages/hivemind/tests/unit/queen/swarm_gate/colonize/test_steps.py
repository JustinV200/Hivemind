"""Tests for hivemind.queen.swarm_gate.colonize.steps: the orders, the pause and the resume.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/steps.py (codingrules section 3). A device is a
    connected link whose packet is scripted (`ScriptedPacket`); a device's Warden is a link
    attached to a real Queen whose far end the test reads; tasks are really RUNNING on it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.steps for the module under test.
    - tests.unit.queen.cluster.test_protocol for the pause path these steps reuse.
"""

from __future__ import annotations

import pytest
from builders.memory import make_handoff
from builders.quarantine import settle
from builders.swarm import GateRig, ScriptedPacket, place_on_device
from builders.swarm.gate import IdleDevice

from hivemind.brood_chamber import TaskStatus
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.queen.attach import detach_warden
from hivemind.queen.cluster import protocol
from hivemind.queen.swarm_gate import GateContext
from hivemind.queen.swarm_gate.colonize import ColonizeStepError
from hivemind.queen.swarm_gate.colonize import steps as steps_module
from hivemind.queen.swarm_gate.colonize.steps import (
    MOVING_REASON,
    Move,
    StepOrder,
    dismiss_remote,
    pause_for_move,
    resume_all,
    send_order,
    tell_device,
)
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.messages import HandoffRef
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome, NodeUpdate

_TINY_S = 0.05  # Real seconds for an order's deadline when the test wants the packet silent.


async def _packet(rig: GateRig) -> tuple[SwarmNode, IdleDevice, ScriptedPacket]:
    """Enrol and connect a device, with its packet scripted."""
    node = await rig.enrol()
    device = await rig.connect(node)
    return node, device, ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)


def _prepare() -> StepOrder:
    """A PREPARE order for the runtime the tests use."""
    return StepOrder(ColonizeAction.PREPARE, 5.0, "Because.", runtime_version="1.0.0")


async def test_an_order_goes_to_the_packet_with_its_fields_and_its_answer_comes_back(
    rig: GateRig,
) -> None:
    node, device, packet = await _packet(rig)

    reply = await send_order(device.link, node, _prepare())

    assert reply.outcome is ColonizeOutcome.PREPARED
    [order] = packet.orders
    assert order.action is ColonizeAction.PREPARE and order.runtime_version == "1.0.0"
    assert (order.cell_id, order.device_id, order.warden_id) == (
        node.cell_id,
        node.device_id,
        node.warden_id,
    )
    assert order.reason == "Because." and order.deadline_s == 5.0
    await packet.close()


async def test_a_start_order_carries_the_url_the_warden_dials(rig: GateRig) -> None:
    node, device, packet = await _packet(rig)
    step = StepOrder(ColonizeAction.START, 5.0, "Because.", warden_url="ws://127.0.0.1:9999")

    reply = await send_order(device.link, node, step)

    assert reply.outcome is ColonizeOutcome.STARTED and reply.warden_pid is not None
    assert packet.orders[0].warden_url == "ws://127.0.0.1:9999"
    await packet.close()


async def test_a_step_the_packet_reports_failed_is_returned_not_raised(rig: GateRig) -> None:
    node, device, packet = await _packet(rig)
    packet.outcomes[ColonizeAction.PREPARE] = ColonizeOutcome.FAILED

    reply = await send_order(device.link, node, _prepare())

    assert reply.outcome is ColonizeOutcome.FAILED  # The flow reads the outcome and decides.
    await packet.close()


async def test_a_packet_that_refuses_the_order_is_one_stable_word(rig: GateRig) -> None:
    node, device, packet = await _packet(rig)
    packet.refused.add(ColonizeAction.PREPARE)

    with pytest.raises(ColonizeStepError) as raised:
        await send_order(device.link, node, _prepare())

    assert raised.value.word == "packet_refused"
    await packet.close()


async def test_a_packet_that_never_answers_is_one_stable_word(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    node, device, packet = await _packet(rig)
    packet.silent.add(ColonizeAction.PREPARE)
    monkeypatch.setattr(steps_module, "REPLY_MARGIN_S", _TINY_S)
    step = StepOrder(ColonizeAction.PREPARE, _TINY_S, "Because.", runtime_version="1.0.0")

    with pytest.raises(ColonizeStepError) as raised:
        await send_order(device.link, node, step)

    assert raised.value.word == "packet_silent"
    await packet.close()


async def test_a_link_that_is_gone_is_one_stable_word(rig: GateRig) -> None:
    node, device, packet = await _packet(rig)
    await packet.close()
    await device.link.close()

    with pytest.raises(ColonizeStepError) as raised:
        await send_order(device.link, node, _prepare())

    assert raised.value.word == "link_lost"


async def test_an_answer_that_is_not_a_colonized_is_one_stable_word(rig: GateRig) -> None:
    node, device, packet = await _packet(rig)
    packet.wrong.add(ColonizeAction.PREPARE)

    with pytest.raises(ColonizeStepError) as raised:
        await send_order(device.link, node, _prepare())

    assert raised.value.word == "unexpected_reply"
    await packet.close()


async def test_the_device_is_told_its_new_rung(rig: GateRig) -> None:
    node, device, packet = await _packet(rig)
    colonized = node.model_copy(update={"level": RuntimeLevel.COLONIZED})

    await tell_device(device.link, colonized, "The Warden moved.")
    await settle(lambda: bool(packet.updates))

    [update] = packet.updates
    assert isinstance(update, NodeUpdate) and update.level is RuntimeLevel.COLONIZED
    assert update.reason == "The Warden moved."
    await packet.close()


async def test_telling_a_device_over_a_failing_link_never_raises(rig: GateRig) -> None:
    node, device, packet = await _packet(rig)
    await packet.close()
    await device.link.close()

    await tell_device(device.link, node, "The Warden moved.")  # Told again when it connects.


async def test_pausing_stops_each_running_task_and_notes_the_handoff_it_wrote(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        end = placed.end
        assert end is not None
        deps = rig.queen_deps
        ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
        ref = await write_checkpoint(make_handoff(deps.clock), placed.task.id, ctx)
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, deps), move, "Colonizing.")

        assert move.paused == (placed.task.id,)
        assert move.refs == {placed.task.id: ref}
        assert (await deps.chamber.get(placed.task.id)).status is TaskStatus.PAUSED
        await end.pump_until(lambda: bool(end.task_pauses))  # The bee was told.


async def test_a_move_waits_for_a_bee_inside_a_command_and_notes_the_handoff_it_then_writes(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A Drone on a device is mostly inside a command, and writes its Handoff only when the command
    # returns. The Warden the move ends takes its bees with it, so the Queen waits (this test is
    # about that wait; the rig's other tests give it no time) and notes what the bee then wrote.
    monkeypatch.setattr(steps_module, "HANDOFF_PATIENCE_S", 5.0)
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        deps = rig.queen_deps
        ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
        written: list[HandoffRef] = []

        async def command_returns(_seconds: float) -> None:
            written.append(await write_checkpoint(make_handoff(deps.clock), placed.task.id, ctx))

        monkeypatch.setattr(protocol, "_pause", command_returns)
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, deps), move, "Colonizing.")

        assert len(written) == 1  # It looked once more, found the Handoff and stopped waiting.
        assert move.refs == {placed.task.id: written[0]}
        assert (await deps.chamber.get(placed.task.id)).status is TaskStatus.PAUSED


async def test_a_task_whose_bee_wrote_no_handoff_is_paused_with_none_noted(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, rig.queen_deps), move, "Colonizing.")

        assert move.refs == {placed.task.id: None}  # It resumes from its last acceptance state.


async def test_resuming_sends_each_task_to_the_warden_with_its_handoff_inline(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        assert placed.end is not None
        deps = rig.queen_deps
        ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
        ref = await write_checkpoint(make_handoff(deps.clock), placed.task.id, ctx)
        move = Move(placed.node, placed.device.link)
        context = GateContext(rig.queen, deps)
        await pause_for_move(context, move, "Colonizing.")

        stranded = await resume_all(context, move, "The Warden moved.")

        assert stranded == ()
        assert (await deps.chamber.get(placed.task.id)).status is TaskStatus.RUNNING
        assignment = await placed.end.wait_for_assignment()
        assert assignment.resume_from == ref
        assert assignment.handoff is not None  # A Warden on the device cannot read her store.


async def test_a_task_the_dispatcher_could_not_resume_is_returned_still_paused(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        move = Move(placed.node, placed.device.link)
        context = GateContext(rig.queen, rig.queen_deps)
        await pause_for_move(context, move, "Colonizing.")
        await detach_warden(rig.queen, placed.node.warden_id)  # No Warden to resume it through.

        stranded = await resume_all(context, move, "The Warden moved.")

        assert stranded == (placed.task.id,)


async def test_dismissing_a_warden_forgets_its_link_and_closes_its_connection(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        context = GateContext(rig.queen, rig.queen_deps)

        await dismiss_remote(context, rig.wardens, placed.node)

        assert rig.queen.wardens == ()
        assert not placed.link.transport.is_connected
        await dismiss_remote(context, rig.wardens, placed.node)  # Twice is fine.


async def test_the_cell_is_held_from_the_pause_and_opened_again_by_the_resume(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        move = Move(placed.node, placed.device.link)
        context = GateContext(rig.queen, rig.queen_deps)
        held = rig.queen_deps.dispatch.held

        await pause_for_move(context, move, "Colonizing.")
        assert held.reason(placed.node.cell_id) == MOVING_REASON  # Nothing lands on it meanwhile.

        await resume_all(context, move, "The Warden moved.")
        assert not held.is_held(placed.node.cell_id)
