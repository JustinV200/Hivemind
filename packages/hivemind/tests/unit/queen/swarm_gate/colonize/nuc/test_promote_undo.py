"""Tests for hivemind.queen.swarm_gate.colonize.nuc.promote when a step fails: the rollback.

A promotion that fails leaves the device as it was: a Warden that answers FAILED, never answers,
or is lost mid-order costs the Queen the ceilings she set (put back), the order she sent (ended
with a DEMOTE unless the Warden cannot be asked), and the request (answered with one stable word
and cleared). A promotion cut short by the gate stopping does the same.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc/promote.py (codingrules section 3), split
    by feature (14.2) from test_promote.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.promote for the module under test.
"""

from __future__ import annotations

import pytest
from builders.quarantine import settle, settle_for
from builders.swarm import GateRig, NucDevice, make_nuc_device
from builders.swarm.nuc import GIB

from hivemind.forage import Ceilings
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize.nuc import desk as desk_module
from hivemind.queen.swarm_gate.colonize.nuc import promote as promote_module
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import NucAction, NucOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.
_SHORT_S = 0.05  # A deadline and a margin short enough for a silent Warden to be given up on.


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def _requested(rig: GateRig, device: NucDevice) -> None:
    """Ask for the Nuc rung and wait for the flow that answers it to end."""
    assert await rig.colonizer.request(device.node)
    await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)


async def _left_as_it_was(rig: GateRig, device: NucDevice) -> None:
    """Assert the device is still colonized and its request was answered once."""
    row = await rig.hive.registry.node(device.node.device_id)
    assert row is not None
    assert row.level is RuntimeLevel.COLONIZED and row.desired_level is None
    assert await rig.hive.events("swarm.promoted") == []


async def test_a_warden_that_fails_the_start_leaves_the_device_colonized_and_says_why_once(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.outcomes[NucAction.PROMOTE] = NucOutcome.FAILED

        await _requested(rig, device)

        await _left_as_it_was(rig, device)
        assert await _decisions(rig) == [("nuc_failed", "promote_failed")]
        await device.close()


async def test_a_failed_promotion_gives_back_the_ceilings_it_replaced(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.outcomes[NucAction.PROMOTE] = NucOutcome.FAILED

        await _requested(rig, device)

        await settle(lambda: len(device.warden.ceilings) == 2, _FLOW_ROUNDS)
        first, back = device.warden.ceilings
        assert first.ceilings.loadable_sources == ("small", "large")
        assert back.ceilings.loadable_sources == ()  # The first dispatch's: nothing allowlisted.
        held = rig.queen_deps.ledger.decisions.ceilings_for(device.node.warden_id)
        assert held is not None and held.loadable_sources == ()
        await device.close()


async def test_a_failed_promotion_orders_the_server_ended_in_case_it_started(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.outcomes[NucAction.PROMOTE] = NucOutcome.FAILED

        await _requested(rig, device)

        assert device.warden.actions == [NucAction.PROMOTE, NucAction.DEMOTE]
        await device.close()


async def test_ceilings_set_for_a_promotion_and_put_back_are_both_decisions_on_the_trail(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.outcomes[NucAction.PROMOTE] = NucOutcome.FAILED

        await _requested(rig, device)

        rows = await rig.queen_deps.trail.query(TrailQuery(kind="forage.ceilings_set"))
        assert len(rows) == 2  # Set for the server, then put back: both decisions are on the trail.
        await device.close()


async def test_a_warden_that_never_answers_is_given_up_on_without_asking_it_anything_else(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(promote_module, "PROMOTE_DEADLINE_S", _SHORT_S)
    monkeypatch.setattr(desk_module, "REPLY_MARGIN_S", _SHORT_S)
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.silent.add(NucAction.PROMOTE)

        # A silent Warden is given up on after a real deadline, which a fast loop's yields can
        # spend before it passes; wait in real time, not in scheduling turns.
        assert await rig.colonizer.request(device.node)
        await settle_for(lambda: not rig.colonizer.busy(device.node.device_id))

        await _left_as_it_was(rig, device)
        assert await _decisions(rig) == [("nuc_failed", "warden_silent")]
        assert device.warden.actions == [NucAction.PROMOTE]  # No DEMOTE: it cannot answer one.
        await device.close()


async def test_a_warden_lost_mid_order_ends_the_wait_at_once(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")

        async def vanish(order: object) -> None:
            await device.warden.drop()

        device.warden.before = vanish
        device.warden.silent.add(NucAction.PROMOTE)

        await _requested(rig, device)  # The deadline is minutes: only the loss can end this.

        await _left_as_it_was(rig, device)
        assert await _decisions(rig) == [("nuc_failed", "link_lost")]
        await device.close()


async def test_a_promotion_cut_short_by_the_gate_stopping_puts_the_ceilings_back(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        device.warden.silent.add(NucAction.PROMOTE)
        assert await rig.colonizer.request(device.node)
        await settle(lambda: device.warden.actions == [NucAction.PROMOTE], _FLOW_ROUNDS)

        await rig.colonizer.stop()  # The gate is stopping: the flow is cancelled and undoes itself.

        assert not rig.colonizer.busy(device.node.device_id)
        await settle(lambda: len(device.warden.ceilings) == 2, _FLOW_ROUNDS)
        assert device.warden.actions == [NucAction.PROMOTE, NucAction.DEMOTE]
        assert await rig.hive.events("swarm.promoted") == []
        await device.close()


async def test_a_warden_with_ceilings_already_set_gets_them_changed_and_changed_back(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")
        first = rig.queen_deps.ledger.decisions
        await first.record_ceilings(
            device.node.warden_id,
            _ceilings(max_sub_bees=3, vram=GIB, loadable=("kept",)),
        )
        device.warden.outcomes[NucAction.PROMOTE] = NucOutcome.FAILED

        await _requested(rig, device)

        await settle(lambda: len(device.warden.ceilings) == 2, _FLOW_ROUNDS)
        raised, restored = (c.ceilings for c in device.warden.ceilings)
        assert (raised.max_sub_bees, raised.loadable_sources) == (3, ("small", "large"))
        assert (restored.max_sub_bees, restored.loadable_sources) == (3, ("kept",))
        assert restored.model_vram_bytes == GIB  # Exactly what she held before.
        await device.close()


def _ceilings(*, max_sub_bees: int, vram: int, loadable: tuple[str, ...]) -> Ceilings:
    return Ceilings(
        max_sub_bees=max_sub_bees,
        model_vram_bytes=vram,
        model_disk_bytes=0,
        loadable_sources=loadable,
        exportable_seats=0,
    )
