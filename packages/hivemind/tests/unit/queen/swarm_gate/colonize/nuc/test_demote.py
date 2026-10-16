"""Tests for hivemind.queen.swarm_gate.colonize.nuc.demote: taking a device's server away.

A Nuc is lowered by ordering its Warden to stop the server. Only once the Warden confirmed does the
rung move, and then the Queen tidies what the Nuc left in the Hive: the allowlist of models is
emptied, the device's sources leave her map, and its hosting plan is written from the shared pool
alone. A Warden that does not confirm leaves the device a Nuc, with the failure recorded.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc/demote.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.demote for the module under test.
"""

from __future__ import annotations

import sqlite3

import pytest
from builders.forage import make_source
from builders.quarantine import settle, settle_for
from builders.swarm import GateRig, NucDevice, make_nuc_device
from builders.swarm.nuc import GIB

from hivemind.forage import Ceilings
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate.colonize.nuc import demote as demote_module
from hivemind.queen.swarm_gate.colonize.nuc import desk as desk_module
from hivemind.swarm import RuntimeLevel
from waggle.messages.swarm import NucAction, NucOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.
_SHORT_S = 0.05  # A deadline and a margin short enough for a silent Warden to be given up on.


async def _a_nuc(rig: GateRig) -> NucDevice:
    """A Nuc that asks to be lowered, with two models on the Queen's map and an allowlist set."""
    device = await make_nuc_device(
        rig, "box", level=RuntimeLevel.NUC, wanted=RuntimeLevel.COLONIZED
    )
    cell_id = device.node.cell_id
    for name in ("small", "large"):
        await rig.queen_deps.map.add_source(
            make_source(source_id=name, model=f"{name}-model", host_cell_id=cell_id, seats=2)
        )
        await rig.queen_deps.ledger.seats.set_capacity(name, 2)
    await rig.queen_deps.ledger.decisions.record_ceilings(
        device.node.warden_id,
        Ceilings(
            max_sub_bees=2,
            model_vram_bytes=16 * GIB,
            model_disk_bytes=0,
            loadable_sources=("small", "large"),
            exportable_seats=0,
        ),
    )
    return device


async def _requested(rig: GateRig, device: NucDevice) -> None:
    """Ask to be lowered and wait for the flow that answers it to end."""
    assert await rig.colonizer.request(device.node)
    await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def test_a_nuc_that_asks_to_be_lowered_is_colonized_again_and_the_request_is_answered(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)

        await _requested(rig, device)

        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None
        assert row.level is RuntimeLevel.COLONIZED and row.desired_level is None
        [demoted] = await rig.hive.events("swarm.demoted")
        assert (demoted.payload["from_level"], demoted.payload["to_level"]) == ("NUC", "COLONIZED")
        [order] = device.warden.orders
        assert order.action is NucAction.DEMOTE and order.server_id is None
        assert order.initial_models == ()
        await device.close()


async def test_the_server_is_stopped_before_the_rung_moves(rig: GateRig) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)
        seen: list[RuntimeLevel] = []

        async def look(order: object) -> None:
            row = await rig.hive.registry.node(device.node.device_id)
            assert row is not None
            seen.append(row.level)

        device.warden.before = look

        await _requested(rig, device)

        assert seen == [RuntimeLevel.NUC]  # Still a Nuc while the Warden is stopping its server.
        await device.close()


async def test_the_devices_models_leave_the_queens_map_and_their_seats_are_zeroed(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)
        await rig.queen_deps.map.add_source(make_source(source_id="elsewhere", host_cell_id=None))
        others = {s.source_id for s in rig.queen_deps.map.sources()} - {"small", "large"}

        await _requested(rig, device)

        # Only the device's own models left; every other source is where it was.
        assert {s.source_id for s in rig.queen_deps.map.sources()} == others
        assert rig.queen_deps.ledger.seats.free("small") == 0
        await device.close()


async def test_the_allowlist_is_emptied_so_nothing_outlives_the_server(rig: GateRig) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)

        await _requested(rig, device)

        await settle(lambda: bool(device.warden.ceilings), _FLOW_ROUNDS)
        [emptied] = device.warden.ceilings
        assert emptied.ceilings.loadable_sources == ()
        assert emptied.ceilings.model_vram_bytes == 16 * GIB  # Nothing else about them changed.
        await device.close()


async def test_the_hosting_plan_is_written_again_from_the_shared_pool_alone(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)
        await rig.queen_deps.map.add_source(
            make_source(source_id="shared", model="shared-model", host_cell_id=None)
        )

        await _requested(rig, device)

        await settle(lambda: bool(device.warden.plans), _FLOW_ROUNDS)
        plan = device.warden.plans[-1]
        assert plan.default.primary.source_id not in {"small", "large"}  # It hosts nothing now.
        assert plan.default.primary.host_cell_id is None
        assert all(c.host_cell_id is None for c in plan.default.fallbacks)
        await device.close()


async def test_a_warden_that_fails_to_stop_leaves_the_device_a_nuc_and_says_why_once(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _a_nuc(rig)
        sources = len(rig.queen_deps.map.sources())
        device.warden.outcomes[NucAction.DEMOTE] = NucOutcome.FAILED
        device.warden.reply_level = RuntimeLevel.NUC

        await _requested(rig, device)

        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.level is RuntimeLevel.NUC and row.desired_level is None
        assert await _decisions(rig) == [("rung_failed", "demote_failed")]
        assert await rig.hive.events("swarm.demoted") == []
        assert len(rig.queen_deps.map.sources()) == sources  # Nothing was tidied: it may run.
        await device.close()


async def test_a_warden_that_never_answers_leaves_the_device_a_nuc(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demote_module, "DEMOTE_DEADLINE_S", _SHORT_S)
    monkeypatch.setattr(desk_module, "REPLY_MARGIN_S", _SHORT_S)
    async with rig.serving():
        device = await _a_nuc(rig)
        device.warden.silent.add(NucAction.DEMOTE)

        # A silent Warden is given up on after a real deadline, which a fast loop's yields can
        # spend before it passes; wait in real time, not in scheduling turns.
        assert await rig.colonizer.request(device.node)
        await settle_for(lambda: not rig.colonizer.busy(device.node.device_id))

        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.level is RuntimeLevel.NUC
        assert await _decisions(rig) == [("rung_failed", "warden_silent")]
        await device.close()


async def test_tidying_that_fails_after_the_server_stopped_does_not_undo_the_demotion(
    rig: GateRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("the ledger is locked")

    monkeypatch.setattr(demote_module, "write_hosting_plan", failing)
    async with rig.serving():
        device = await _a_nuc(rig)

        await _requested(rig, device)

        row = await rig.hive.registry.node(device.node.device_id)
        assert row is not None and row.level is RuntimeLevel.COLONIZED
        await device.close()
