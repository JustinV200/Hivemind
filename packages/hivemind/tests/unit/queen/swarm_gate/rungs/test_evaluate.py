"""Tests for hivemind.queen.swarm_gate.rungs.evaluate: the Queen colonizing a device herself.

The evaluator is the part of the Queen's own rung decisions that touches the world. Each pass it
reads the Hive once, meets the devices heaviest first, and for a device nobody asked to move it
launches the step `decide_rung` chose, through the colonizer, so the Guard is asked and the flow is
the operator's own. These tests run it against a gate rig whose devices are scripted: a Level 0
device with bees running on a Hive Stand of a chosen size, a packet that answers the install, and a
Warden that dials in when the packet starts it. What is asserted is what she launched and why (the
reason on the `swarm.promoted` it writes), what she left alone, and what she does not repeat.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/evaluate.py (codingrules section 3), split by
    feature (14.2): the climb to Level 1 here, the way back down in test_evaluate_lowering.py and
    the climb to a Nuc in test_evaluate_nuc.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.evaluate for the module under test.
    - hivemind.queen.swarm_gate.rungs.decide for the decision it acts on.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from builders.quarantine import settle
from builders.queen import with_guard_policy
from builders.swarm import (
    GateRig,
    ScriptedPacket,
    make_evaluator,
    make_promotion,
    report_stand,
    run_on,
    run_pass,
    survival_task,
)

from hivemind.cell import AccessLevel
from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen
from hivemind.queen.swarm_gate import Colonizer, GateContext
from hivemind.queen.swarm_gate.rungs import RungEvaluator
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def test_a_hive_stand_under_pressure_has_a_device_colonized_for_relief(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await report_stand(rig, cores=1)  # The one bee takes all of the machine.
        evaluator = make_evaluator(rig)

        launched = await run_pass(rig, evaluator)

        assert launched == {scenario.device_id: True}
        await scenario.finish()
        assert (await scenario.node()).level is RuntimeLevel.COLONIZED
        assert scenario.packet.actions == [ColonizeAction.PREPARE, ColonizeAction.START]
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "relief" in str(promoted.payload["reason"])  # Hers, not the operator's.
        await scenario.close()


async def test_a_hive_stand_under_its_threshold_has_nothing_colonized(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await report_stand(rig, cores=2)  # One bee in two cores: 0.5, under the 0.8 threshold.

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {scenario.device_id: False}
        assert scenario.packet.orders == []
        await scenario.close()


async def test_a_device_with_no_bee_running_is_not_colonized_for_relief_it_could_not_give(
    rig: GateRig,
) -> None:
    async with rig.serving():
        loaded, idle = await make_promotion(rig), await make_promotion(rig)
        for scenario in (loaded, idle):
            await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await rig.hive.registry.set_ceiling(loaded.device_id, RuntimeLevel.GATEWAY)  # Not hers.
        await rig.queen_deps.chamber.cancel(idle.placed.task.id, "The work ended.")
        await report_stand(rig, cores=1)  # The Hive Stand is full, by the pinned device's bee.

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {loaded.device_id: False, idle.device_id: False}
        assert idle.packet.orders == [] and loaded.packet.orders == []
        await loaded.close()
        await idle.close()


async def test_a_hive_stand_whose_size_is_not_known_is_never_relieved(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await run_on(rig, scenario.placed.node, count=10)  # Plenty of bees, on a machine unsized.

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {scenario.device_id: False}
        assert scenario.packet.orders == []
        await scenario.close()


async def test_a_task_pinned_to_a_device_that_must_outlive_its_link_has_the_device_colonized(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        node = scenario.placed.node
        await survival_task(rig.queen_deps, pinned_to=node.cell_id)  # Waiting; nowhere else to go.

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {scenario.device_id: True}
        await scenario.finish()
        assert (await scenario.node()).level is RuntimeLevel.COLONIZED
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "outage" in str(promoted.payload["reason"])
        await scenario.close()


async def test_a_task_running_on_a_device_that_must_outlive_its_link_has_the_device_colonized(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await run_on(rig, scenario.placed.node, survives=True)

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {scenario.device_id: True}
        await scenario.finish()
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "outage" in str(promoted.payload["reason"])
        await scenario.close()


async def test_a_task_that_does_not_ask_to_outlive_its_link_moves_nothing(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await survival_task(rig.queen_deps, survives=False, pinned_to=scenario.placed.node.cell_id)

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {scenario.device_id: False}
        assert scenario.packet.orders == []
        await scenario.close()


async def test_one_device_is_colonized_for_relief_at_a_time_and_the_heaviest_goes_first(
    rig: GateRig,
) -> None:
    async with rig.serving():
        light, heavy = await make_promotion(rig), await make_promotion(rig)
        for scenario in (light, heavy):
            await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await run_on(rig, heavy.placed.node, count=2)  # Three bees against the light one's one.
        await report_stand(rig, cores=4)  # Four bees on four cores: the stand is full.
        evaluator = make_evaluator(rig)

        launched = await run_pass(rig, evaluator)

        assert list(launched.items()) == [(heavy.device_id, True), (light.device_id, False)]
        await heavy.finish()
        assert (await heavy.node()).level is RuntimeLevel.COLONIZED
        assert light.packet.orders == []  # Judged on the next pass, on the figure the move changed.
        await light.close()
        await heavy.close()


async def test_the_figure_a_move_changed_is_read_again_before_the_next_device_is_moved(
    rig: GateRig,
) -> None:
    async with rig.serving():
        first, second = await make_promotion(rig), await make_promotion(rig)
        for scenario in (first, second):
            await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await run_on(rig, second.placed.node, count=2)
        await report_stand(rig, cores=4)
        evaluator = make_evaluator(rig)
        await run_pass(rig, evaluator)
        await second.finish()

        launched = await run_pass(rig, evaluator)  # The first device's bee is all that is left.

        assert not any(launched.values())
        assert first.packet.orders == []
        await first.close()
        await second.close()


async def test_a_device_with_a_request_pending_is_left_to_the_operators_request(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)  # The operator's wish stands.
        await report_stand(rig, cores=1)
        evaluator = make_evaluator(rig)
        await evaluator.begin_pass(await rig.hive.registry.nodes())
        node = await scenario.node()

        assert await evaluator.evaluate(node) is False

        assert scenario.packet.orders == []
        await scenario.close()


async def test_a_device_the_operator_pinned_at_the_gateway_is_not_colonized(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await rig.hive.registry.set_ceiling(scenario.device_id, RuntimeLevel.GATEWAY)
        await report_stand(rig, cores=1)  # Pressure, and a task that must survive, both hold.
        await run_on(rig, scenario.placed.node, survives=True)

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {scenario.device_id: False}
        assert scenario.packet.orders == []
        await scenario.close()


async def test_a_device_a_flow_already_owns_is_not_started_again(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        scenario.packet.silent.add(ColonizeAction.PREPARE)  # The install never finishes.
        await report_stand(rig, cores=1)
        evaluator = make_evaluator(rig)
        await run_pass(rig, evaluator)
        await settle(lambda: bool(scenario.packet.orders))  # The flow is waiting on its packet.

        launched = await run_pass(rig, evaluator)

        assert launched == {scenario.device_id: True}  # Owned: reported so, and left alone.
        assert scenario.packet.actions == [ColonizeAction.PREPARE]
        await scenario.close()


async def test_a_colonization_that_fails_is_not_tried_again_until_its_wait_has_run_out(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        scenario.packet.outcomes[ColonizeAction.PREPARE] = ColonizeOutcome.FAILED
        await report_stand(rig, cores=1)
        evaluator = make_evaluator(rig)
        await run_pass(rig, evaluator)
        await scenario.finish()

        assert await run_pass(rig, evaluator) == {scenario.device_id: False}  # Backing off.
        assert scenario.packet.actions.count(ColonizeAction.PREPARE) == 1

        rig.clock.advance(rig.terms.retry_after_s)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}  # Its wait is over.
        await scenario.finish()
        assert scenario.packet.actions.count(ColonizeAction.PREPARE) == 2
        await scenario.close()


async def test_a_second_failure_in_a_row_waits_twice_as_long(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        scenario.packet.outcomes[ColonizeAction.PREPARE] = ColonizeOutcome.FAILED
        await report_stand(rig, cores=1)
        evaluator, wait = make_evaluator(rig), rig.terms.retry_after_s
        await run_pass(rig, evaluator)  # The first attempt fails...
        await scenario.finish()
        await run_pass(rig, evaluator)  # ... and the pass after settles it: one wait.
        rig.clock.advance(wait)
        await run_pass(rig, evaluator)  # The second attempt fails...
        await scenario.finish()
        await run_pass(rig, evaluator)  # ... and now it is two waits, from now.

        rig.clock.advance(wait)
        assert await run_pass(rig, evaluator) == {scenario.device_id: False}  # One of two.
        rig.clock.advance(wait)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}  # Both.
        await scenario.finish()
        assert scenario.packet.actions.count(ColonizeAction.PREPARE) == 3
        await scenario.close()


async def test_a_guard_that_denies_the_move_is_asked_again_only_when_the_wait_has_run_out(
    rig: GateRig,
) -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("warden:spawn",))})
    deps = with_guard_policy(rig.queen_deps, load_guard_policy(None, section))
    context = GateContext(Queen(deps), deps)
    colonizer = Colonizer(rig.wardens, rig.hive.registry, rig.listener, rig.terms, lambda: None)
    colonizer.bind(context)
    rig.bind()
    async with asyncio.TaskGroup() as group:
        colonizer.open(group)
        node = await rig.enrol()
        await rig.connect(node)
        packet = ScriptedPacket(rig.devices[node.device_id], rig.clock, rig.hive.voice.hive_id)
        await survival_task(deps, pinned_to=node.cell_id)
        evaluator = RungEvaluator(colonizer, rig.hive.hub, rig.clock, rig.terms)
        evaluator.bind(context)

        async def denials() -> int:
            return len(await deps.trail.query(TrailQuery(kind="guard.denied")))

        await run_pass(rig, evaluator)
        await run_pass(rig, evaluator)  # Not asked again at once.
        assert await denials() == 1
        rig.clock.advance(rig.terms.retry_after_s)
        await run_pass(rig, evaluator)

        assert await denials() == 2
        assert packet.orders == []  # Nothing was ever sent to the device.
        await packet.close()
        colonizer.close()


async def test_a_reason_that_holds_for_a_device_that_cannot_carry_a_warden_is_recorded_once(
    rig: GateRig,
) -> None:
    node = await rig.enrol(access_level=AccessLevel.READ_ONLY)
    await rig.connect(node)
    await survival_task(rig.queen_deps, pinned_to=node.cell_id)
    evaluator = make_evaluator(rig)

    for _ in range(3):
        assert await run_pass(rig, evaluator) == {node.device_id: False}

    assert await _decisions(rig) == [("rung_declined", "access_too_low")]  # One line, not three.


async def test_a_device_with_no_reason_is_not_recorded_as_refused(rig: GateRig) -> None:
    node = await rig.enrol(access_level=AccessLevel.READ_ONLY)
    await rig.connect(node)

    await run_pass(rig, make_evaluator(rig))

    assert await _decisions(rig) == []  # Nobody wanted it colonized, so nothing was refused.


async def test_an_evaluator_that_was_never_bound_to_a_queen_decides_nothing(rig: GateRig) -> None:
    node = await rig.enrol()
    await rig.connect(node)
    evaluator = RungEvaluator(rig.colonizer, rig.hive.hub, rig.clock, rig.terms)

    assert await evaluator.begin_pass([node]) == (node,)
    assert await evaluator.evaluate(node) is False


async def test_a_hive_she_cannot_read_is_one_she_moves_nothing_in_and_the_pass_goes_on(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await report_stand(rig, cores=1)

        async def unreadable(_context: GateContext, _nodes: object) -> object:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr("hivemind.queen.swarm_gate.rungs.evaluate.read_world", unreadable)
        evaluator = make_evaluator(rig)

        nodes = await evaluator.begin_pass(await rig.hive.registry.nodes())  # Raises nothing.

        assert [node.device_id for node in nodes] == [scenario.device_id]
        assert await evaluator.evaluate(nodes[0]) is False
        assert scenario.packet.orders == []
        await scenario.close()
