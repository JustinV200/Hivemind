"""Tests for hivemind.queen.swarm_gate.colonize.nuc.promote: giving a colonized device a server.

The whole flow runs against a scripted device Warden connected to the gate's door. What is
asserted is what the Queen sends and in which order (the ceilings, then the order), what she
leaves behind when it works (the rung, the trail, a hosting plan with the device's own models
first) and when it does not (the ceilings she replaced, the rung, the request).

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc/promote.py (codingrules section 3); the
    failures and the rollback are in test_promote_undo.py (split by feature, 14.2).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.promote for the module under test.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder.
"""

from __future__ import annotations

import sqlite3

import pytest
from builders.quarantine import settle
from builders.swarm import GateRig, NucDevice, make_nuc_device
from builders.swarm.nuc import GIB

from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize.nuc import promote as promote_module
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import NucAction

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.


async def _promoted(rig: GateRig, device: NucDevice) -> None:
    """Ask for the Nuc rung and wait for the flow that answers it to end."""
    assert await rig.colonizer.request(device.node)
    await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)


async def test_a_colonized_device_that_asks_becomes_a_nuc_and_the_request_is_answered(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")

        await _promoted(rig, device)

        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None
        assert row.level is RuntimeLevel.NUC and row.desired_level is None
        [promoted] = await rig.hive.events("swarm.promoted")
        assert (promoted.payload["from_level"], promoted.payload["to_level"]) == (
            "COLONIZED",
            "NUC",
        )
        assert promoted.payload["device_id"] == device.node.device_id
        hub_node = rig.hive.hub.node(device.node.device_id)
        assert hub_node is not None and hub_node.level is RuntimeLevel.NUC
        await device.close()


async def test_the_ceilings_are_set_before_the_warden_is_asked_to_start_a_server(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")

        await _promoted(rig, device)

        assert device.warden.received[:2] == ["ceilings", "order:PROMOTE"]
        [ceilings] = device.warden.ceilings
        assert ceilings.ceilings.model_vram_bytes == 16 * GIB
        assert ceilings.ceilings.loadable_sources == ("small", "large")
        await device.close()


async def test_the_order_names_the_server_the_models_to_load_first_and_a_deadline(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box", free_vram_gib=8.0)

        await _promoted(rig, device)

        [order] = device.warden.orders
        assert order.action is NucAction.PROMOTE and order.server_id == "box"
        assert order.initial_models == ("small",)  # The 12 GiB model never fits this device.
        assert order.cell_id == device.node.cell_id and order.deadline_s > 0
        [ceilings] = device.warden.ceilings
        assert ceilings.ceilings.loadable_sources == ("small",)
        await device.close()


async def test_the_ceilings_the_queen_sets_are_on_the_trail_and_in_the_ledger(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")

        await _promoted(rig, device)

        [row] = await rig.queen_deps.trail.query(TrailQuery(kind="forage.ceilings_set"))
        assert row.payload["cell_id"] == device.node.cell_id
        held = rig.queen_deps.ledger.decisions.ceilings_for(device.node.warden_id)
        assert held is not None and held.loadable_sources == ("small", "large")
        await device.close()


async def test_the_hosting_plan_puts_the_devices_own_models_first(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")

        await _promoted(rig, device)

        await settle(lambda: bool(device.warden.plans), _FLOW_ROUNDS)
        assert device.warden.received[-1] == "plan"  # Written once the server was reported up.
        plan = device.warden.plans[-1]
        assert plan.cell_id == device.node.cell_id
        # The shared pool holds nothing in this rig, so every primary is one of the device's own.
        assert plan.default.primary.host_cell_id == device.node.cell_id
        assert plan.default.primary.source_id in {"small", "large"}
        [plan_row] = await rig.queen_deps.trail.query(TrailQuery(kind="forage.plan_written"))
        assert plan_row.subject_id == device.node.cell_id
        await device.close()


async def test_the_models_the_warden_says_it_loaded_are_the_ones_planned_for(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.loaded = ("small",)  # It loaded one of the two it was asked for.

        await _promoted(rig, device)

        await settle(lambda: bool(device.warden.plans), _FLOW_ROUNDS)
        plan = device.warden.plans[-1]
        assert plan.default.primary.source_id == "small"
        assert all(c.source_id != "large" for c in plan.default.fallbacks)
        await device.close()


async def test_a_plan_that_cannot_be_written_does_not_undo_the_promotion(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("the ledger is locked")

    monkeypatch.setattr(promote_module, "write_hosting_plan", failing)
    async with rig.serving():
        device = await make_nuc_device(rig, "box")

        await _promoted(rig, device)

        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.level is RuntimeLevel.NUC  # The server is up regardless.
        await device.close()
