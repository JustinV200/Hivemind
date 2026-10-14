"""Tests for hivemind.queen.swarm_gate.colonize.flow: a Nuc promotion the Queen decided herself.

The Queen promotes a colonized device to a Nuc on her own reasons (the shared seats are under
pressure, a task must survive a lost link), and the step she chooses runs as the same flow the
operator's request does, behind the same Guard check, with her reason on the `swarm.promoted` it
writes. There is no request to answer or clear.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/flow.py (codingrules section 3), split by
    feature (14.2) from ../test_flow_launch.py, which needs no model server.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.flow for the module under test.
"""

from __future__ import annotations

from builders.quarantine import settle
from builders.swarm import GateRig, make_nuc_device

from hivemind.queen.swarm_gate.colonize import Ask, Step
from hivemind.swarm import NucDecision, NucReason, RuntimeLevel

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.


async def test_a_nuc_step_the_queen_chose_starts_the_server_on_her_reason(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box", wanted=RuntimeLevel.COLONIZED)
        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.desired_level is None
        decision = NucDecision(
            NucReason.PRESSURE, None, "box", ("small", "large"), ("small", "large")
        )

        word = await rig.colonizer.launch(row, Ask(Step.NUC_PROMOTE, "pressure", decision))
        await settle(lambda: not rig.colonizer.busy(row.device_id), _FLOW_ROUNDS)

        assert word is None
        after = await rig.hive.registry.node(row.device_id)
        assert after is not None and after.level is RuntimeLevel.NUC
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "pressure" in str(promoted.payload["reason"])
        await device.close()
