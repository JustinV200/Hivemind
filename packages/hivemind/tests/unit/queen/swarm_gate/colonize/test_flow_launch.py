"""Tests for hivemind.queen.swarm_gate.colonize.flow: a rung change the Queen decided herself.

The operator's request and the Queen's own decision are one path once the step is chosen: the same
Guard check, the same flows, the same trail events. What differs is that nothing is answered or
cleared, because no request waits, and a refusal comes back as a word for the caller to remember
instead of a row and a cleared wish. These tests drive `Colonizer.launch` with the steps
`hivemind.queen.swarm_gate.rungs` would choose.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/flow.py (codingrules section 3), split by
    feature (14.2) from test_flow.py, which covers the operator's request.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.flow for the module under test.
"""

from __future__ import annotations

import asyncio

from builders.quarantine import settle
from builders.queen import with_guard_policy
from builders.swarm import GateRig, ScriptedPacket, make_promotion

from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen
from hivemind.queen.swarm_gate import Colonizer, GateContext
from hivemind.queen.swarm_gate.colonize import (
    GUARD_DENIED_WORD,
    NOT_READY_WORD,
    OWNED_WORD,
    Ask,
    Step,
)
from hivemind.swarm import NucDecision, NucReason, RuntimeLevel
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.
_PROMOTE = Ask(Step.PROMOTE, "relief")


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def test_a_step_the_queen_chose_runs_as_the_same_flow_with_her_reason_on_the_trail(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)  # Nobody asked.

        assert await rig.colonizer.launch(await scenario.node(), _PROMOTE) is None
        await scenario.finish()

        assert (await scenario.node()).level is RuntimeLevel.COLONIZED
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "relief" in str(promoted.payload["reason"])
        assert await _decisions(rig) == []  # Nothing refused, nothing failed, nothing to clear.
        await scenario.close()


async def test_a_flow_that_fails_is_recorded_and_leaves_no_wish_behind(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        await rig.hive.registry.set_desired_level(scenario.device_id, None)
        scenario.packet.outcomes[ColonizeAction.PREPARE] = ColonizeOutcome.FAILED

        assert await rig.colonizer.launch(await scenario.node(), _PROMOTE) is None
        await scenario.finish()

        row = await scenario.node()
        assert row.level is RuntimeLevel.GATEWAY and row.desired_level is None
        assert ("colonize_failed", "prepare_failed") in await _decisions(rig)
        await scenario.close()


async def test_a_device_with_a_flow_running_is_owned_and_nothing_is_started(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.silent.add(ColonizeAction.PREPARE)
        assert await scenario.start()
        await settle(lambda: bool(scenario.packet.orders))

        assert await rig.colonizer.launch(await scenario.node(), _PROMOTE) == OWNED_WORD

        assert scenario.packet.actions == [ColonizeAction.PREPARE]
        await scenario.close()


async def test_a_guard_that_denies_the_rung_returns_the_word_and_records_no_refusal(
    rig: GateRig,
) -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("warden:spawn",))})
    deps = with_guard_policy(rig.queen_deps, load_guard_policy(None, section))
    colonizer = Colonizer(rig.wardens, rig.hive.registry, rig.listener, rig.terms, lambda: None)
    colonizer.bind(GateContext(Queen(deps), deps))
    rig.bind()
    async with asyncio.TaskGroup() as group:
        colonizer.open(group)
        node = await rig.enrol()
        await rig.connect(node)
        packet = ScriptedPacket(rig.devices[node.device_id], rig.clock, rig.hive.voice.hive_id)

        assert await colonizer.launch(node, _PROMOTE) == GUARD_DENIED_WORD

        assert packet.orders == []  # Nothing was sent to the device.
        denials = await deps.trail.query(TrailQuery(kind="guard.denied"))
        assert len(denials) == 1
        assert await deps.trail.query(TrailQuery(kind="queen.decided")) == ()  # Hers to remember.
        await packet.close()
        colonizer.close()


async def test_a_device_with_no_packet_link_is_not_ready(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()  # Never connected.

        assert await rig.colonizer.launch(node, _PROMOTE) == NOT_READY_WORD


async def test_a_gate_that_is_not_running_is_not_ready_and_asks_the_guard_nothing(
    rig: GateRig,
) -> None:
    rig.bind()
    node = await rig.enrol()
    await rig.connect(node)  # The colonizer has no group to run a flow in.

    assert await rig.colonizer.launch(node, _PROMOTE) == NOT_READY_WORD
    assert await rig.queen_deps.trail.query(TrailQuery(kind="guard.denied")) == ()


async def test_a_step_of_none_starts_nothing(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.connect(node)

        assert await rig.colonizer.launch(node, Ask(None, "not_asked")) == NOT_READY_WORD


async def test_a_nuc_step_with_no_warden_to_speak_to_is_not_ready(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED)
        await rig.connect(node)  # Its packet is up; its own Warden never dialled in.
        decision = NucDecision(NucReason.PRESSURE, None, "box", ("small",), ("small",))

        word = await rig.colonizer.launch(node, Ask(Step.NUC_PROMOTE, "pressure", decision))

        assert word == NOT_READY_WORD
        assert not rig.colonizer.busy(node.device_id)
