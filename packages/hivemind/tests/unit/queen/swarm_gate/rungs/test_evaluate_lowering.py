"""Tests for hivemind.queen.swarm_gate.rungs.evaluate: the Queen taking a rung back, not too soon.

A rung the Queen raised is hers to take back, but only once nothing asks for it any more, for the
whole of `lower_after_s`, and only while the device is idle; a rung the operator raised is never
hers to take back, and a device the operator pinned below its rung is taken down as soon as it is
idle. A move that fails is not tried again until its wait has run out. These tests raise a device
with the evaluator itself, on a clock they move by hand, and then watch what each pass does as the
reason goes, comes back, and the device is busy or quiet.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/evaluate.py (codingrules section 3), split by
    feature (14.2) from test_evaluate.py, which covers the climb.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.evaluate for the module under test.
    - hivemind.queen.swarm_gate.rungs.keeper for the memory that makes the waits.
"""

from __future__ import annotations

from builders.swarm import (
    GateRig,
    PromotionScenario,
    make_evaluator,
    make_promotion,
    report_state,
    run_on,
    run_pass,
    survival_task,
)

from hivemind.brood_chamber import Task
from hivemind.queen.swarm_gate import open_tasks
from hivemind.queen.swarm_gate.rungs import RungEvaluator
from hivemind.swarm import RuntimeLevel
from waggle.messages.supervision import WardenState
from waggle.messages.swarm import ColonizeAction


async def _raised(rig: GateRig) -> tuple[PromotionScenario, RungEvaluator, Task]:
    """A device the Queen colonized for a task pinned to it that must outlive its link.

    Returns:
        The scenario, the evaluator, and the pinned task whose existence is the reason; the
        pass after the move has run, so the rung is marked as hers.
    """
    scenario = await make_promotion(rig)
    await rig.hive.registry.set_desired_level(scenario.device_id, None)
    pinned = await survival_task(rig.queen_deps, pinned_to=scenario.placed.node.cell_id)
    evaluator = make_evaluator(rig)
    await run_pass(rig, evaluator)
    await scenario.finish()
    await run_pass(rig, evaluator)  # The pass after a move settles it: the rung is hers now.
    assert (await scenario.node()).level is RuntimeLevel.COLONIZED
    return scenario, evaluator, pinned


async def _end_the_work(rig: GateRig, scenario: PromotionScenario) -> None:
    """End every task placed on the device and have its Warden report that it only watches."""
    node = scenario.placed.node
    for task in await open_tasks(rig.queen_deps):
        if task.cell_id == node.cell_id:
            await rig.queen_deps.chamber.cancel(task.id, "The work ended.")
    report_state(rig, node, WardenState.WATCH)


async def _end_the_reason(rig: GateRig, pinned: Task) -> None:
    """End the task that asked for the rung."""
    await rig.queen_deps.chamber.cancel(pinned.id, "The goal ended.")


def _withdrawals(scenario: PromotionScenario) -> int:
    """How many times the device's packet has been told to stop its Warden."""
    return scenario.packet.actions.count(ColonizeAction.WITHDRAW)


async def test_a_rung_is_kept_for_as_long_as_the_reason_it_was_raised_for_holds(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario, evaluator, _pinned = await _raised(rig)
        await _end_the_work(rig, scenario)  # Idle, and the task that wants it is still there.
        rig.clock.advance(10 * rig.terms.lower_after_s)

        launched = await run_pass(rig, evaluator)

        assert launched == {scenario.device_id: False}
        assert _withdrawals(scenario) == 0
        await scenario.close()


async def test_a_rung_whose_reasons_are_gone_is_lowered_after_the_whole_wait_and_not_before(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario, evaluator, pinned = await _raised(rig)
        await _end_the_work(rig, scenario)
        await _end_the_reason(rig, pinned)
        wait = rig.terms.lower_after_s

        rig.clock.advance(wait - 1)
        assert await run_pass(rig, evaluator) == {scenario.device_id: False}  # One second short.
        rig.clock.advance(1)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}

        await scenario.finish()
        assert (await scenario.node()).level is RuntimeLevel.GATEWAY
        [demoted] = await rig.hive.events("swarm.demoted")
        assert "reasons_gone" in str(demoted.payload["reason"])
        await scenario.close()


async def test_a_reason_that_comes_back_gives_the_rung_the_whole_wait_again(rig: GateRig) -> None:
    async with rig.serving():
        scenario, evaluator, pinned = await _raised(rig)
        await _end_the_work(rig, scenario)
        await _end_the_reason(rig, pinned)
        wait = rig.terms.lower_after_s
        cell = scenario.placed.node.cell_id

        rig.clock.advance(wait * 2 / 3)
        assert await run_pass(rig, evaluator) == {scenario.device_id: False}
        again = await survival_task(rig.queen_deps, pinned_to=cell)  # Wanted once more...
        assert await run_pass(rig, evaluator) == {scenario.device_id: False}
        await _end_the_reason(rig, again)  # ... and gone again.

        rig.clock.advance(wait * 2 / 3)  # More than the wait since the first, not since the last.
        assert await run_pass(rig, evaluator) == {scenario.device_id: False}
        rig.clock.advance(wait / 3)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}
        await scenario.finish()
        await scenario.close()


async def test_a_device_with_work_on_it_is_not_taken_back_however_long_its_reasons_are_gone(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario, evaluator, pinned = await _raised(rig)
        await _end_the_reason(rig, pinned)
        await run_on(rig, scenario.placed.node)  # A bee at work on the device.
        report_state(rig, scenario.placed.node, WardenState.WATCH)
        rig.clock.advance(10 * rig.terms.lower_after_s)

        assert await run_pass(rig, evaluator) == {scenario.device_id: False}
        assert _withdrawals(scenario) == 0

        await _end_the_work(rig, scenario)  # The wait was over long ago; only the work held it.
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}
        await scenario.finish()
        await scenario.close()


async def test_a_warden_that_has_not_said_it_is_watching_is_not_idle(rig: GateRig) -> None:
    async with rig.serving():
        scenario, evaluator, pinned = await _raised(rig)
        await _end_the_work(rig, scenario)
        await _end_the_reason(rig, pinned)
        report_state(rig, scenario.placed.node, WardenState.ACTIVE)
        rig.clock.advance(rig.terms.lower_after_s)

        assert await run_pass(rig, evaluator) == {scenario.device_id: False}

        report_state(rig, scenario.placed.node, WardenState.WATCH)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}
        await scenario.finish()
        await scenario.close()


async def test_a_rung_the_operator_raised_is_never_taken_back_by_the_queen(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)  # The operator's wish: the Queen did not raise it.
        assert await scenario.start()
        await scenario.finish()
        assert (await scenario.node()).desired_level is None  # Carried out; nothing pending.
        await _end_the_work(rig, scenario)
        rig.clock.advance(100 * rig.terms.lower_after_s)
        evaluator = make_evaluator(rig)

        assert await run_pass(rig, evaluator) == {scenario.device_id: False}
        assert await run_pass(rig, evaluator) == {scenario.device_id: False}

        assert (await scenario.node()).level is RuntimeLevel.COLONIZED
        assert _withdrawals(scenario) == 0
        await scenario.close()


async def test_a_withdrawal_the_packet_refuses_is_not_tried_again_until_its_wait_has_run_out(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario, evaluator, pinned = await _raised(rig)
        await _end_the_work(rig, scenario)
        await _end_the_reason(rig, pinned)
        scenario.packet.refused.add(ColonizeAction.WITHDRAW)
        rig.clock.advance(rig.terms.lower_after_s)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}
        await scenario.finish()
        assert (await scenario.node()).level is RuntimeLevel.COLONIZED  # Nothing changed.

        assert await run_pass(rig, evaluator) == {scenario.device_id: False}  # Backing off.
        assert _withdrawals(scenario) == 1

        rig.clock.advance(rig.terms.retry_after_s)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}
        await scenario.finish()
        assert _withdrawals(scenario) == 2
        await scenario.close()


async def test_a_device_the_operator_pinned_below_its_rung_is_taken_down_once_it_is_idle(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        assert await scenario.start()  # The operator raised it...
        await scenario.finish()
        await rig.hive.registry.set_ceiling(
            scenario.device_id, RuntimeLevel.GATEWAY
        )  # ... then pinned it.
        report_state(rig, scenario.placed.node, WardenState.WATCH)
        evaluator = make_evaluator(rig)

        assert await run_pass(rig, evaluator) == {scenario.device_id: False}  # Work on it still.
        assert _withdrawals(scenario) == 0

        await _end_the_work(rig, scenario)  # No wait: the operator's word, not the Queen's.
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}
        await scenario.finish()
        assert (await scenario.node()).level is RuntimeLevel.GATEWAY
        [demoted] = await rig.hive.events("swarm.demoted")
        assert "pinned" in str(demoted.payload["reason"])
        await scenario.close()


async def test_a_device_pinned_at_the_gateway_is_not_colonized_again_whatever_asks(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        assert await scenario.start()
        await scenario.finish()
        await rig.hive.registry.set_ceiling(scenario.device_id, RuntimeLevel.GATEWAY)
        await _end_the_work(rig, scenario)
        evaluator = make_evaluator(rig)
        await run_pass(rig, evaluator)
        await scenario.finish()
        await survival_task(rig.queen_deps, pinned_to=scenario.placed.node.cell_id)

        assert await run_pass(rig, evaluator) == {scenario.device_id: False}

        assert (await scenario.node()).level is RuntimeLevel.GATEWAY
        assert scenario.packet.actions.count(ColonizeAction.PREPARE) == 1  # Only the operator's.
        await scenario.close()


async def test_work_placed_on_a_device_since_the_pass_read_the_hive_stops_the_withdrawal(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario, evaluator, pinned = await _raised(rig)
        await _end_the_work(rig, scenario)
        await _end_the_reason(rig, pinned)
        rig.clock.advance(rig.terms.lower_after_s)
        [node] = await evaluator.begin_pass(await rig.hive.registry.nodes())  # Idle, as read.
        await run_on(rig, scenario.placed.node)  # And now the dispatcher places a task on it.

        assert await evaluator.evaluate(node) is False
        await scenario.finish()  # A flow that had been started would be over by now.

        assert _withdrawals(scenario) == 0
        await _end_the_work(rig, scenario)
        assert await run_pass(rig, evaluator) == {scenario.device_id: True}  # Only once idle again.
        await scenario.finish()
        await scenario.close()
