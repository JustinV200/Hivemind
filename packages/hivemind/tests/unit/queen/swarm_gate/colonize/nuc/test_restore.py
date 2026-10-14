"""Tests for hivemind.queen.swarm_gate.colonize.nuc.restore: a Nuc across a Warden restart.

A Nuc's model server is its Warden's child, so a Warden that restarts under a device recorded as a
Nuc runs none. The flow runs when that Warden connects, and the registry must never go on saying
the device has a server it has not: either the server is started again, from what the Queen set for
the Warden, or the rung is lowered and the Hive tidied exactly as an operator's demotion would. A
Warden that cannot be reached decides nothing, and the device is tried again. The Warden is a
scripted end of a connection the gate's door admitted, restarted by `restart_nuc_warden`.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc/restore.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.restore for the module under test.
    - hivemind.queen.swarm_gate.colonize.nuc.promote for the promotion whose order this repeats.
"""

from __future__ import annotations

import pytest
from builders.quarantine import settle, settle_for
from builders.swarm import GateRig, NucDevice, make_nuc_device, restart_nuc_warden
from builders.swarm.nuc import gpu_capacity

from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize.nuc import desk as desk_module
from hivemind.queen.swarm_gate.colonize.nuc import restore as restore_module
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.messages.swarm import NucAction, NucOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.
_SHORT_S = 0.05  # A deadline and a margin short enough for a silent Warden to be given up on.


async def _a_nuc(rig: GateRig, **kwargs: object) -> NucDevice:
    """A device the Queen promoted, whose first Warden then restarted and declared `box` again."""
    device = await make_nuc_device(rig, "box", **kwargs)  # type: ignore[arg-type]
    assert await rig.colonizer.request(device.node)
    await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)
    await restart_nuc_warden(rig, device, "box")
    return device


async def _row(rig: GateRig, device: NucDevice) -> SwarmNode:
    """The device as the registry holds it now."""
    row = await rig.hive.registry.node(device.node.device_id)
    assert row is not None
    return row


async def _restored(rig: GateRig, device: NucDevice) -> bool:
    """Ask for the restoration and wait for the flow to end; whether one began."""
    started = await rig.colonizer.restore(await _row(rig, device))
    await settle_for(lambda: not rig.colonizer.busy(device.node.device_id))
    return started


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def test_a_warden_that_restarted_is_asked_to_start_its_server_again(rig: GateRig) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)

        assert await _restored(rig, device)

        warden = device.warden
        [order] = warden.orders
        assert order.action is NucAction.PROMOTE and order.server_id == "box"
        assert order.initial_models == ("small", "large")
        row = await _row(rig, device)
        assert row.level is RuntimeLevel.NUC  # And nothing about the rung was written again.
        assert len(await rig.hive.events("swarm.promoted")) == 1
        assert await rig.hive.events("swarm.demoted") == []
        await device.close()


async def test_the_ceilings_the_queen_set_reach_the_new_warden_before_the_order(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)

        await _restored(rig, device)

        received = device.warden.received
        assert "ceilings" in received and received.index("ceilings") < received.index(
            "order:PROMOTE"
        )
        await device.close()


async def test_the_hosting_plan_is_written_again_with_the_devices_own_models_first(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)

        await _restored(rig, device)

        await settle(lambda: device.warden.received[-1] == "plan", _FLOW_ROUNDS)
        plan = device.warden.plans[-1]
        assert plan.default.primary.host_cell_id == device.node.cell_id
        await device.close()


async def test_the_models_are_the_ones_the_queen_allowed_not_what_the_device_has_room_for_now(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig, free_vram_gib=8.0)  # Only `small` fits: that is all she allowed.
        row = await _row(rig, device)
        rig.hive.hub.update_node(row.model_copy(update={"capacity": gpu_capacity(24.0)}))

        await _restored(rig, device)

        [order] = device.warden.orders
        assert order.initial_models == ("small",)
        await device.close()


async def test_a_nuc_whose_server_the_promotion_just_proved_is_not_restored(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        assert await rig.colonizer.request(device.node)
        await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)

        assert not await rig.colonizer.restore(await _row(rig, device))  # Its server answered.
        await device.close()


async def test_a_restoration_proves_the_server_so_it_is_not_asked_again(rig: GateRig) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)
        assert await _restored(rig, device)

        assert not await _restored(rig, device)
        assert len(device.warden.orders) == 1
        await device.close()


async def test_a_device_that_is_not_a_nuc_has_nothing_to_restore(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")  # Colonized, asking for the Nuc rung.

        assert not await rig.colonizer.restore(await _row(rig, device))
        await device.close()


async def test_a_warden_that_cannot_start_the_server_leaves_a_colonized_device_and_says_so(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)
        device.warden.outcomes[NucAction.PROMOTE] = NucOutcome.FAILED

        assert await _restored(rig, device)

        row = await _row(rig, device)
        assert row.level is RuntimeLevel.COLONIZED
        [lowered] = await rig.hive.events("swarm.demoted")
        assert (lowered.payload["from_level"], lowered.payload["to_level"]) == ("NUC", "COLONIZED")
        assert "promote_failed" in str(lowered.payload["reason"])
        assert ("nuc_not_restored", "promote_failed") in await _decisions(rig)
        assert device.warden.actions == [
            NucAction.PROMOTE,
            NucAction.DEMOTE,
        ]  # Whatever runs stops.
        await device.close()


async def test_a_device_recorded_as_a_nuc_with_nothing_to_restore_from_is_lowered(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box", level=RuntimeLevel.NUC)
        await rig.hive.registry.set_desired_level(device.node.device_id, None)

        assert await _restored(rig, device)  # The Queen holds no ceilings for this Warden.

        row = await _row(rig, device)
        assert row.level is RuntimeLevel.COLONIZED
        assert ("nuc_not_restored", "nothing_to_restore") in await _decisions(rig)
        await device.close()


async def test_a_warden_that_never_answers_decides_nothing_and_is_tried_again(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(restore_module, "PROMOTE_DEADLINE_S", _SHORT_S)
    monkeypatch.setattr(desk_module, "REPLY_MARGIN_S", _SHORT_S)
    async with rig.serving():
        device = await _a_nuc(rig)
        device.warden.silent.add(NucAction.PROMOTE)

        assert await _restored(rig, device)

        row = await _row(rig, device)
        assert row.level is RuntimeLevel.NUC  # Not heard from: nothing is decided about it.
        assert await rig.hive.events("swarm.demoted") == []
        assert "nuc_not_restored" not in [reason for reason, _ in await _decisions(rig)]
        device.warden.silent.clear()
        assert await _restored(rig, device)  # Still unproven: the next pass asks again.
        await device.close()
