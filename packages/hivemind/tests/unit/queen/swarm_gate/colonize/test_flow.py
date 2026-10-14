"""Tests for hivemind.queen.swarm_gate.colonize.flow: deciding, authorising and running a request.

The Colonizer is the real class over the rig's registry, hub, Wardens and Queen. A request is
refused, denied by the Guard, or carried out as a flow; what is asserted is the answer it gives,
what it records, and that a device is owned by its flow until the flow ends. The flows themselves
are tested in test_promote.py and test_withdraw.py.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/flow.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.flow for the module under test.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.cells import make_capabilities
from builders.quarantine import settle
from builders.queen import with_guard_policy
from builders.swarm import GateRig, ScriptedPacket, make_promotion

from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen
from hivemind.queen.swarm_gate import Colonizer, GateContext
from hivemind.queen.swarm_gate.colonize import flow as flow_module
from hivemind.swarm import GatewayUnavailableError, RuntimeLevel, SwarmNode
from waggle.ids import new_device_id
from waggle.messages.swarm import ColonizeAction, ColonizeOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def _asking(rig: GateRig, wanted: RuntimeLevel | None, **overrides: object) -> SwarmNode:
    """Enrol and connect a Level 0 device that asks for `wanted`; its row."""
    node = await rig.enrol(**overrides)
    await rig.connect(node)
    if wanted is not None:
        await rig.hive.registry.set_desired_level(node.device_id, wanted)
    row = await rig.hive.registry.node(node.device_id)
    assert row is not None
    return row


async def test_a_device_that_asks_for_nothing_starts_no_flow(rig: GateRig) -> None:
    async with rig.serving():
        node = await _asking(rig, None)

        assert not await rig.colonizer.request(node)

        assert not rig.colonizer.busy(node.device_id)
        assert await _decisions(rig) == []


async def test_a_device_with_no_live_packet_link_keeps_its_request_waiting(rig: GateRig) -> None:
    async with rig.serving():
        node = await rig.enrol()
        await rig.hive.registry.set_desired_level(node.device_id, RuntimeLevel.COLONIZED)
        row = await rig.hive.registry.node(node.device_id)
        assert row is not None

        assert not await rig.colonizer.request(row)

        again = await rig.hive.registry.node(node.device_id)
        assert again is not None and again.desired_level is RuntimeLevel.COLONIZED  # Not answered.
        assert await _decisions(rig) == []


async def test_a_gate_that_is_not_running_starts_no_flow(rig: GateRig) -> None:
    rig.bind()
    node = await _asking(rig, RuntimeLevel.COLONIZED)  # The colonizer has no group to run in.

    assert not await rig.colonizer.request(node)

    assert not rig.colonizer.busy(node.device_id)


async def test_a_device_too_small_to_carry_a_warden_is_refused_once_and_the_request_cleared(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await _asking(
            rig, RuntimeLevel.COLONIZED, capabilities=make_capabilities(python_version="3.11.9")
        )

        assert not await rig.colonizer.request(node)

        assert await _decisions(rig) == [("rung_refused", "python_too_old")]
        row = await rig.hive.registry.node(node.device_id)
        assert row is not None and row.desired_level is None  # Not asked again on every pass.
        assert not rig.colonizer.busy(node.device_id)
        assert await rig.hive.events("swarm.promoted") == []


async def test_a_colonized_device_whose_warden_declared_no_server_is_refused_the_nuc_rung(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await _asking(rig, RuntimeLevel.NUC, level=RuntimeLevel.COLONIZED)

        assert not await rig.colonizer.request(node)

        assert await _decisions(rig) == [("rung_refused", "no_server")]


async def test_a_guard_that_denies_the_rung_stops_the_flow_before_it_touches_the_device(
    rig: GateRig,
) -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("warden:spawn",))})
    deps = with_guard_policy(rig.queen_deps, load_guard_policy(None, section))
    colonizer = Colonizer(rig.wardens, rig.hive.registry, rig.listener, rig.terms, lambda: None)
    colonizer.bind(GateContext(Queen(deps), deps))
    async with asyncio.TaskGroup() as group:
        colonizer.open(group)
        node = await _asking(rig, RuntimeLevel.COLONIZED)
        packet = ScriptedPacket(rig.devices[node.device_id], rig.clock, rig.hive.voice.hive_id)

        assert not await colonizer.request(node)

        assert packet.orders == []  # Nothing was sent to the device.
        denials = await deps.trail.query(TrailQuery(kind="guard.denied"))
        assert [d.payload["capability"] for d in denials] == [f"device:{node.device_id}"]
        events = await deps.trail.query(TrailQuery(kind="queen.decided"))
        assert [(e.payload["reason"], e.payload["detail"]) for e in events] == [
            ("rung_refused", "guard_denied")
        ]
        await packet.close()
        colonizer.close()


async def test_a_device_with_a_flow_is_asked_once_however_often_the_pass_asks(
    rig: GateRig,
) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.silent.add(ColonizeAction.PREPARE)  # The flow waits on the packet.

        assert await scenario.start()
        assert await scenario.start()  # The next pass finds the flow running: still owned.

        await settle(lambda: bool(scenario.packet.orders))
        assert scenario.packet.actions == [ColonizeAction.PREPARE]
        assert rig.colonizer.busy(scenario.device_id)
        await scenario.close()


async def test_the_gate_is_woken_when_a_flow_ends(rig: GateRig) -> None:
    woken: list[bool] = []
    colonizer = Colonizer(
        rig.wardens, rig.hive.registry, rig.listener, rig.terms, lambda: woken.append(True)
    )
    rig.bind()
    colonizer.bind(GateContext(rig.queen, rig.queen_deps))
    async with asyncio.TaskGroup() as group:
        colonizer.open(group)
        node = await _asking(rig, RuntimeLevel.COLONIZED, capabilities=make_capabilities())
        packet = ScriptedPacket(rig.devices[node.device_id], rig.clock, rig.hive.voice.hive_id)
        packet.outcomes[ColonizeAction.PREPARE] = ColonizeOutcome.FAILED

        assert await colonizer.request(node)
        await settle(lambda: not colonizer.busy(node.device_id), _FLOW_ROUNDS)

        assert woken  # The loop looks at the device again at once.
        await packet.close()
        colonizer.close()


async def test_a_wardens_arrival_is_reported_to_the_flow_waiting_for_it(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.on_start = None  # START is answered, and nobody dials yet.

        await scenario.start()
        await settle(lambda: ColonizeAction.START in scenario.packet.actions, _FLOW_ROUNDS)
        await settle(lambda: rig.colonizer.expects(scenario.device_id), _FLOW_ROUNDS)

        assert rig.colonizer.expects(scenario.device_id)  # The door would admit this Warden.
        assert not rig.colonizer.expects(new_device_id(rig.clock))  # And no stranger.
        await scenario.close()


async def test_stop_cancels_every_flow_and_waits_for_each(rig: GateRig) -> None:
    async with rig.serving():
        scenario = await make_promotion(rig)
        scenario.packet.silent.add(ColonizeAction.PREPARE)
        await scenario.start()
        await settle(lambda: bool(scenario.packet.orders))

        await rig.colonizer.stop()

        assert not rig.colonizer.busy(scenario.device_id)
        await scenario.close()


async def test_a_flow_that_fails_outright_is_recorded_and_its_request_cleared(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Broken:
        def __init__(self, *_args: object) -> None:
            pass

        async def run(self) -> None:
            raise GatewayUnavailableError("The trail went away.")

    monkeypatch.setattr(flow_module, "Promotion", _Broken)
    async with rig.serving():
        scenario = await make_promotion(rig)

        assert await scenario.start()
        await scenario.finish()

        assert ("rung_failed", "internal_error") in await _decisions(rig)
        row = await scenario.node()
        assert row.desired_level is None and row.level is RuntimeLevel.GATEWAY
        await scenario.close()
