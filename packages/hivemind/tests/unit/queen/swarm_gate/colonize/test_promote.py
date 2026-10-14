"""Tests for hivemind.queen.swarm_gate.colonize.promote: moving a Warden onto its device.

The whole flow is run against a scripted packet and a scripted device Warden: the packet answers
each `swarm.colonize` order from a script, and when it answers START the device's own Warden dials
the gate's door, exactly as the process the packet launched would. A real Warden serves the device
on Level 0 until it is stopped, and a task is really RUNNING on it. What is asserted is what the
Queen does at each step and what she leaves behind: the rung, the trail, the tasks, the Warden.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/promote.py (codingrules section 3); the failures
    and the rollback are in test_promote_undo.py (split by feature, 14.2).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.promote for the module under test.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the order of the steps.
"""

from __future__ import annotations

from builders.isolation import place_running
from builders.quarantine import settle
from builders.swarm import RUNTIME_VERSION, GateRig, make_promotion
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskStatus
from hivemind.pheromone import TrailQuery
from hivemind.swarm import NodeStatus, RuntimeLevel
from hivemind.wardens import WardenState
from waggle.messages.swarm import ColonizeAction


async def test_the_warden_moves_onto_the_device_and_the_rung_is_recorded(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        old = rig.wardens.get(scenario.device_id)
        assert old is not None

        assert await scenario.start()
        await scenario.finish()

        node = await scenario.node()
        assert node.level is RuntimeLevel.COLONIZED and node.desired_level is None
        assert node.status is NodeStatus.ONLINE
        [promoted] = await rig.hive.events("swarm.promoted")
        assert (promoted.payload["from_level"], promoted.payload["to_level"]) == (
            "GATEWAY",
            "COLONIZED",
        )
        assert promoted.payload["device_id"] == node.device_id
        assert old.warden.state is WardenState.STOPPED  # The Hive Stand's Warden is gone.
        assert rig.wardens.get(node.device_id) is None
        assert [link.warden_id for link in rig.queen.wardens] == [node.warden_id]  # Same id.
        await scenario.close()


async def test_the_orders_to_the_packet_are_prepare_then_start_with_the_runtime_and_the_url(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        await scenario.start()
        await scenario.finish()

        prepare, start = scenario.packet.orders
        assert (
            prepare.action is ColonizeAction.PREPARE and prepare.runtime_version == RUNTIME_VERSION
        )
        assert start.action is ColonizeAction.START and start.warden_url == rig.listener.warden_url
        assert prepare.warden_id == start.warden_id == scenario.placed.node.warden_id
        await scenario.close()


async def test_the_migration_is_on_the_trail_from_the_hive_stands_node_to_the_devices(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        await scenario.start()
        await scenario.finish()

        node = await scenario.node()
        [migrated] = await rig.queen_deps.trail.query(TrailQuery(kind="warden.migrated"))
        assert migrated.subject_id == node.warden_id
        assert migrated.payload["from_node"] == rig.queen_deps.identity.node_id
        assert migrated.payload["to_node"] == node.node_id
        reconnected = await rig.queen_deps.trail.query(TrailQuery(kind="warden.reconnected"))
        assert [e.payload["node_id"] for e in reconnected] == [node.node_id]  # Same Warden, back.
        await scenario.close()


async def test_the_running_task_resumes_on_the_devices_warden_from_its_handoff_inline(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        ref = await scenario.checkpoint()

        await scenario.start()
        await scenario.finish()

        task = await rig.queen_deps.chamber.get(scenario.placed.task.id)
        assert task.status is TaskStatus.RUNNING
        assert task.warden_id == scenario.placed.node.warden_id
        [warden] = scenario.dialled
        assignment = await warden.end.wait_for_assignment()
        assert assignment.task_id == task.id
        assert assignment.resume_from == ref
        assert assignment.handoff is not None  # A Warden on the device cannot read her store.
        assert assignment.handoff.goal == "Finish wiring the new endpoint."
        await scenario.close()


async def test_a_task_whose_bee_wrote_no_handoff_resumes_fresh(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        await scenario.start()
        await scenario.finish()

        [warden] = scenario.dialled
        assignment = await warden.end.wait_for_assignment()
        assert assignment.resume_from is None and assignment.handoff is None
        task = await rig.queen_deps.chamber.get(scenario.placed.task.id)
        assert task.status is TaskStatus.RUNNING
        await scenario.close()


async def test_the_packet_is_told_its_new_rung(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        await scenario.start()
        await scenario.finish()

        await settle(lambda: bool(scenario.packet.updates))  # The packet reads it on its own turn.
        [update] = scenario.packet.updates
        assert update.level is RuntimeLevel.COLONIZED
        assert update.warden_id == scenario.placed.node.warden_id
        await scenario.close()


async def test_the_hubs_record_of_the_device_carries_the_new_rung(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        await scenario.start()
        await scenario.finish()

        recorded = rig.hive.hub.node(scenario.device_id)
        assert recorded is not None and recorded.level is RuntimeLevel.COLONIZED
        await scenario.close()


async def test_the_device_is_left_to_its_flow_until_the_flow_ends(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)

        assert await scenario.start()

        assert rig.colonizer.busy(scenario.device_id)  # The reconcile pass leaves it alone.
        await scenario.finish()
        assert not rig.colonizer.busy(scenario.device_id)
        await scenario.close()


async def test_work_with_no_bee_to_pause_is_taken_back_to_be_placed_afresh(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        deps, node = rig.queen_deps, scenario.placed.node
        [assigned] = await deps.chamber.submit(make_graph_draft({"root": ()}))
        await deps.chamber.assign(
            assigned.id, node.warden_id, node.cell_id, "Placed.", bound_tier=node.comb_shield
        )
        blocked = await place_running(deps, scenario.placed.link)
        await deps.chamber.ask(blocked.id, node.warden_id, "Which one?")

        await scenario.start()
        await scenario.finish()

        assert (await deps.chamber.get(assigned.id)).status is TaskStatus.PENDING
        assert (await deps.chamber.get(blocked.id)).status is TaskStatus.PENDING
        assert (await deps.chamber.get(scenario.placed.task.id)).status is TaskStatus.RUNNING
        events = await deps.trail.query(TrailQuery(kind="queen.decided"))
        assert ("tasks_taken_back", "colonizing") in [
            (e.payload.get("reason"), e.payload.get("detail")) for e in events
        ]
        await scenario.close()


async def test_a_task_clustering_had_already_paused_is_left_paused(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        deps = rig.queen_deps
        waiting = await place_running(deps, scenario.placed.link)
        await deps.chamber.pause(waiting.id, "Clustering: provider unavailable.")

        await scenario.start()
        await scenario.finish()

        # Clustering's own resume wakes it; the move neither resumes nor takes it back.
        assert (await deps.chamber.get(waiting.id)).status is TaskStatus.PAUSED
        await scenario.close()


async def test_the_cell_is_held_while_its_warden_is_on_the_way_and_open_once_it_has_arrived(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.on_start = None  # START is answered, and nobody dials yet.
        held = rig.queen_deps.dispatch.held

        await scenario.start()
        await settle(lambda: ColonizeAction.START in scenario.packet.actions, 40_000)

        assert held.is_held(scenario.placed.node.cell_id)  # Nothing is placed on it meanwhile.
        await scenario.dial(scenario.packet.orders[-1])  # The Warden dials in.
        await scenario.finish()
        assert not held.is_held(scenario.placed.node.cell_id)
        await scenario.close()
