"""Tests for hivemind.queen.swarm_gate.colonize.steps: the terms, the capacity, idleness, the rest.

Four small steps the flows share. `send_terms` is the node update on its own, for a caller that
must know the packet was told. `live_node` puts the capacity the hub last had on the registry's
row. `is_idle` is the test a withdrawal and the Queen's own lowering of a rung wait for. And
`take_back_the_rest` takes what a pause left on a Cell (work that was assigned or blocked and has
no bee to resume) back to be placed afresh.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/steps.py (codingrules section 3), split by
    feature (14.2) from test_steps.py, which covers the orders, the pause and the resume.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.steps for the module under test.
"""

from __future__ import annotations

from builders.forage import make_capacity
from builders.quarantine import settle
from builders.swarm import (
    GateRig,
    ScriptedPacket,
    attach_device_warden,
    place_on_device,
    report_state,
)
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import AccessLevel
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate import GateContext
from hivemind.queen.swarm_gate.colonize.steps import (
    Move,
    is_idle,
    live_node,
    pause_for_move,
    send_terms,
    take_back_the_rest,
)
from hivemind.swarm import RuntimeLevel
from waggle.ids import TaskId
from waggle.messages.supervision import WardenState
from waggle.messages.swarm import NodeUpdate


async def test_the_packet_is_sent_the_terms_the_registry_now_holds(rig: GateRig) -> None:
    node = await rig.enrol()
    device = await rig.connect(node)
    packet = ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
    narrowed = node.model_copy(update={"access_level": AccessLevel.READ_ONLY})

    sent = await send_terms(device.link, narrowed, "The operator narrowed it.")
    await settle(lambda: bool(packet.updates))

    [update] = packet.updates
    assert sent is True and isinstance(update, NodeUpdate)
    assert update.access_level.value == "READ_ONLY" and update.reason == "The operator narrowed it."
    await packet.close()


async def test_terms_that_could_not_be_sent_are_reported_as_not_sent(rig: GateRig) -> None:
    node = await rig.enrol()
    device = await rig.connect(node)
    await device.link.close()

    assert await send_terms(device.link, node, "The operator narrowed it.") is False


async def test_the_capacity_the_hub_last_had_replaces_the_registrys(rig: GateRig) -> None:
    node = await rig.enrol()
    await rig.connect(node)
    fresher = make_capacity(max_sub_bees=1)
    rig.hive.hub.update_node(node.model_copy(update={"capacity": fresher}))

    live = live_node(rig.hive.hub, node)

    assert live.capacity == fresher
    assert live.model_copy(update={"capacity": node.capacity}) == node  # And nothing else changed.


async def test_a_device_the_hub_does_not_hold_is_returned_as_it_is(rig: GateRig) -> None:
    node = await rig.enrol()  # Enrolled, never connected.

    assert live_node(rig.hive.hub, node) is node


async def test_a_warden_that_observes_and_holds_no_task_is_idle(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await attach_device_warden(rig, node)
    report_state(rig, node, WardenState.WATCH)

    assert await is_idle(GateContext(rig.queen, rig.queen_deps), node)


async def test_a_warden_that_has_not_reported_is_not_idle(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await attach_device_warden(rig, node)

    assert not await is_idle(GateContext(rig.queen, rig.queen_deps), node)


async def test_a_warden_that_is_not_watching_is_not_idle(rig: GateRig) -> None:
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    await attach_device_warden(rig, node)
    report_state(rig, node, WardenState.ACTIVE)

    assert not await is_idle(GateContext(rig.queen, rig.queen_deps), node)


async def test_a_warden_with_a_task_on_its_cell_is_not_idle_whatever_it_reports(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        report_state(rig, placed.node, WardenState.WATCH)

        assert not await is_idle(GateContext(rig.queen, rig.queen_deps), placed.node)


async def test_what_the_pause_left_on_the_cell_is_taken_back_and_what_it_paused_is_not(
    rig: GateRig,
) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        deps = rig.queen_deps
        waiting = await _assigned_on(rig, placed.link)
        context = GateContext(rig.queen, deps)
        move = Move(placed.node, placed.device.link)
        await pause_for_move(context, move, "Moving.")

        await take_back_the_rest(context, move, "colonizing")

        assert (await deps.chamber.get(waiting)).status is TaskStatus.PENDING  # No bee to resume.
        assert (await deps.chamber.get(placed.task.id)).status is TaskStatus.PAUSED  # Resumes.


async def _assigned_on(rig: GateRig, link: WardenLink) -> TaskId:
    """Place a task on `link`'s Cell that no bee has started: assigned, not running."""
    deps = rig.queen_deps
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    tier = link.cell.comb_shield
    await deps.chamber.assign(task.id, link.warden_id, link.cell.id, "Placed.", bound_tier=tier)
    return task.id
