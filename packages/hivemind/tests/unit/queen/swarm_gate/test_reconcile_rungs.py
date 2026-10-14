"""Tests for hivemind.queen.swarm_gate.reconcile: how a pass reaches the Queen's own decisions.

The gate's reconcile pass is the only caller of the Queen's rung evaluator, so what it hands her
matters as much as what she does with it. A pass reads the Hive once and meets the heaviest device
first; it hands her a reachable device that has no request, whether its Warden is on the Hive Stand
or on the device; it never hands her a device that is lost, one with an operator's request (which
the colonizer carries out), or one it is restoring or renewing this pass. These tests run whole
sweeps over a rig and either watch what the Queen then does or count whom she was asked about.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/reconcile.py (codingrules section 3), split by feature
    (14.2) from test_reconcile.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.reconcile for the module under test.
    - hivemind.queen.swarm_gate.rungs for the decisions it hands devices to.
"""

from __future__ import annotations

import pytest
from builders.swarm import (
    GateRig,
    ScriptedPacket,
    attach_device_warden,
    make_evaluator,
    make_promotion,
    make_reconciler,
    report_stand,
    run_on,
)

from hivemind.queen.swarm_gate.rungs import RungEvaluator
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.ids import DeviceId


def _asked(evaluator: RungEvaluator, monkeypatch: pytest.MonkeyPatch) -> list[DeviceId]:
    """Count the devices the evaluator is asked about, in order, while still deciding for them."""
    asked: list[DeviceId] = []
    decide = evaluator.evaluate

    async def evaluate(node: SwarmNode) -> bool:
        asked.append(node.device_id)
        return await decide(node)

    monkeypatch.setattr(evaluator, "evaluate", evaluate)
    return asked


async def _colonized(rig: GateRig) -> SwarmNode:
    """A device on Level 1 whose own Warden is attached to the Queen and open, with no request."""
    node = await rig.enrol(level=RuntimeLevel.COLONIZED)
    device = await rig.connect(node)
    ScriptedPacket(device, rig.clock, rig.hive.voice.hive_id)
    await attach_device_warden(rig, node)
    return node


async def test_a_sweep_hands_a_reachable_device_with_no_request_to_the_queens_decisions(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await report_stand(rig, cores=1)
        reconciler = make_reconciler(rig, make_evaluator(rig))

        await reconciler.sweep()
        await scenario.finish()

        assert (await scenario.node()).level is RuntimeLevel.COLONIZED
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "relief" in str(promoted.payload["reason"])
        await scenario.close()


async def test_a_sweep_meets_the_heaviest_device_first_so_it_is_the_one_relieved(
    rig: GateRig,
) -> None:
    async with rig.serving():
        light, heavy = await make_promotion(rig), await make_promotion(rig)
        for scenario in (light, heavy):
            await rig.hive.registry.set_desired_level(scenario.device_id, None)
        await run_on(rig, heavy.placed.node, count=2)
        await report_stand(rig, cores=4)
        reconciler = make_reconciler(rig, make_evaluator(rig))

        await reconciler.sweep()
        await heavy.finish()

        assert (await heavy.node()).level is RuntimeLevel.COLONIZED
        assert (await light.node()).level is RuntimeLevel.GATEWAY
        assert light.packet.orders == []
        await light.close()
        await heavy.close()


async def test_a_sweep_does_not_hand_over_a_device_with_a_request_the_colonizer_carries_out(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)  # The operator asked for the next rung.
        await report_stand(rig, cores=1)  # And the Hive Stand is full, which would be her reason.
        evaluator = make_evaluator(rig)
        asked = _asked(evaluator, monkeypatch)

        await make_reconciler(rig, evaluator).sweep()
        await scenario.finish()

        assert (await scenario.node()).level is RuntimeLevel.COLONIZED
        assert asked == []  # The operator's request, not hers: no decision, no reason of hers.
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "operator" in str(promoted.payload["reason"])
        assert "relief" not in str(promoted.payload["reason"])
        await scenario.close()


async def test_a_sweep_does_not_hand_over_a_device_it_cannot_reach(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        await rig.enrol()  # Online in the registry, but there is no link to it.
        evaluator = make_evaluator(rig)
        asked = _asked(evaluator, monkeypatch)

        await make_reconciler(rig, evaluator).sweep()

        assert asked == []


async def test_a_sweep_hands_over_a_reachable_level_0_device_once_its_warden_is_served(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)
        evaluator = make_evaluator(rig)
        asked = _asked(evaluator, monkeypatch)
        reconciler = make_reconciler(rig, evaluator)

        await reconciler.sweep()  # The first pass gives it a Warden...
        assert asked == []
        await reconciler.sweep()  # ... and the next one has her judge it.

        assert asked == [node.device_id]


async def test_a_sweep_hands_over_a_colonized_device_whose_warden_is_connected(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        node = await _colonized(rig)
        evaluator = make_evaluator(rig)
        asked = _asked(evaluator, monkeypatch)

        await make_reconciler(rig, evaluator).sweep()

        assert asked == [node.device_id]


async def test_a_sweep_does_not_hand_over_a_colonized_device_whose_warden_is_gone(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)
        await rig.connect(node)  # The packet is up; the device's own Warden never dialled.
        evaluator = make_evaluator(rig)
        asked = _asked(evaluator, monkeypatch)

        await make_reconciler(rig, evaluator).sweep()

        assert asked == []


async def test_a_sweep_that_restores_a_nuc_does_not_hand_it_over_in_the_same_pass(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with rig.serving():
        await _colonized(rig)
        evaluator = make_evaluator(rig)
        asked = _asked(evaluator, monkeypatch)

        async def restoring(_node: SwarmNode) -> bool:
            return True  # A flow now owns the device.

        monkeypatch.setattr(rig.colonizer, "restore", restoring)

        await make_reconciler(rig, evaluator).sweep()

        assert asked == []
