"""Tests for hivemind.queen.swarm_gate.colonize.flow on the Nuc rungs: asking, refusing, waiting.

The colonizer reads a pending request, makes the Nuc decision from what the device's Warden
declared and what the Hive allows, asks the Guard, and starts a promotion or a demotion; or it
refuses once, with one word, and clears the request; or, when the device's own Warden is not
attached, it waits. The flows themselves are in nuc/test_promote.py and nuc/test_demote.py.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/flow.py (codingrules section 3), split by
    feature (14.2) from test_flow.py, which holds the first rung's paths.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.flow for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from builders.quarantine import settle
from builders.queen import with_guard_policy
from builders.swarm import (
    GateRig,
    capacity_frame,
    gpu_capacity,
    make_gate_rig,
    make_nuc_device,
    nuc_terms,
)

from hivemind.guard import load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery
from hivemind.queen import Queen
from hivemind.queen.swarm_gate import Colonizer, GateContext
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import NucAction

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.


@pytest.fixture
async def rig() -> AsyncIterator[GateRig]:
    """A swarm gate over fakes that allows `box`, nothing started."""
    built = make_gate_rig(nuc_terms())
    yield built
    await built.close()


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def test_the_servers_a_warden_declares_are_kept_for_the_decision(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box", "other")

        assert rig.colonizer.declared(device.node.device_id) == ("box", "other")
        await device.close()


@pytest.mark.parametrize(
    ("declared", "free_gib", "word"),
    [
        ((), 16.0, "no_server"),  # The operator declared nothing on the device.
        (("elsewhere",), 16.0, "no_allowlist"),  # A server the Hive's manifest does not list.
        (("box",), 2.0, "vram_too_small"),  # Smaller than the smallest model it may load.
    ],
)
async def test_a_device_that_cannot_carry_a_server_is_refused_once_and_the_request_cleared(
    rig: GateRig, declared: tuple[str, ...], free_gib: float, word: str
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, *declared, free_vram_gib=free_gib)

        assert not await rig.colonizer.request(device.node)

        assert await _decisions(rig) == [("rung_refused", word)]
        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.desired_level is None  # Not asked again on every pass.
        assert (
            device.warden.received == []
        )  # Nothing was sent to the Warden: no ceilings, no order.
        assert not rig.colonizer.busy(device.node.device_id)
        await device.close()


async def test_a_nuc_request_waits_while_the_devices_own_warden_is_not_attached(
    rig: GateRig,
) -> None:
    async with rig.serving():
        node = await rig.enrol(level=RuntimeLevel.COLONIZED, capacity=gpu_capacity(16.0))
        await rig.connect(node)  # Its packet is up; its Warden has not dialled in.
        await rig.hive.registry.set_desired_level(node.device_id, RuntimeLevel.NUC)
        row = await rig.hive.registry.node(node.device_id)
        assert row is not None
        rig.colonizer.heard(row, capacity_frame(rig, row, "box"))  # It declared before.

        assert not await rig.colonizer.request(row)

        kept = await rig.hive.registry.node(node.device_id)
        assert kept is not None and kept.desired_level is RuntimeLevel.NUC  # The request waits.
        assert not rig.colonizer.busy(node.device_id)


async def test_the_nuc_decision_is_made_on_the_capacity_the_hub_last_heard(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box", free_vram_gib=2.0)  # The registry's row.
        grown = device.node.model_copy(update={"capacity": gpu_capacity(16.0)})
        rig.hive.hub.update_node(grown)  # The packet has since reported a much freer GPU.

        assert await rig.colonizer.request(device.node)
        await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)

        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.level is RuntimeLevel.NUC
        assert await _decisions(rig) == []
        await device.close()


async def test_a_guard_that_denies_the_nuc_rung_sends_the_warden_nothing(rig: GateRig) -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("warden:spawn",))})
    deps = with_guard_policy(rig.queen_deps, load_guard_policy(None, section))
    colonizer = Colonizer(rig.wardens, rig.hive.registry, rig.listener, rig.terms, lambda: None)
    colonizer.bind(GateContext(Queen(deps), deps))
    rig.bind()
    async with asyncio.TaskGroup() as group:
        colonizer.open(group)
        device = await make_nuc_device(rig, "box")
        colonizer.heard(device.node, capacity_frame(rig, device.node, "box"))

        assert not await colonizer.request(device.node)

        assert device.warden.received == []
        denials = await deps.trail.query(TrailQuery(kind="guard.denied"))
        assert [d.payload["capability"] for d in denials] == [f"device:{device.node.device_id}"]
        await device.close()
        colonizer.close()


async def test_a_nuc_demotion_request_starts_a_flow_that_owns_the_device(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(
            rig, "box", level=RuntimeLevel.NUC, wanted=RuntimeLevel.COLONIZED
        )
        device.warden.silent.add(NucAction.DEMOTE)  # The flow waits on the Warden.

        assert await rig.colonizer.request(device.node)
        assert await rig.colonizer.request(device.node)  # The next pass finds it still owned.

        await settle(lambda: device.warden.actions == [NucAction.DEMOTE], _FLOW_ROUNDS)
        assert rig.colonizer.busy(device.node.device_id)
        await device.close()
