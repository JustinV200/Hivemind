"""Tests for hivemind.queen.swarm_gate.rungs.evaluate: the Queen giving a device a model server.

A colonized device whose Warden declared a model server is given it when the shared seats are held
past `[swarm.nuc] seat_pressure` or a task on it must survive a lost link, and the server is taken
away again only when those reasons have been gone for `nuc_lower_after_s` and the device is idle. A
reason on a device that cannot carry a server (none declared, no room in its VRAM) is recorded once.
These tests run the evaluator against a colonized device whose Warden is scripted to answer the
Queen's orders, on a Hive whose seats and tasks they set.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/evaluate.py (codingrules section 3), split by
    feature (14.2) from test_evaluate.py, which needs no model server.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.evaluate for the module under test.
    - hivemind.swarm.nuc for the reasons and the refusals it acts on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.forage import make_grant
from builders.quarantine import settle
from builders.swarm import (
    GateRig,
    NucDevice,
    make_evaluator,
    make_gate_rig,
    make_nuc_device,
    nuc_terms,
    report_state,
    run_on,
    run_pass,
)

from hivemind.forage import ForageGrant, GrantState, SeatReservation
from hivemind.pheromone import TrailQuery
from hivemind.queen.swarm_gate import open_tasks
from hivemind.swarm import RuntimeLevel, SwarmNode
from waggle.messages.supervision import WardenState
from waggle.messages.swarm import NucAction, NucOutcome

_FLOW_ROUNDS = 40_000  # Scheduling turns a whole flow may take.
_SEATS = 11  # Declared on one source; the Royal Reserve keeps one, so ten are the pool's to give.


@pytest.fixture
async def rig() -> AsyncIterator[GateRig]:
    """A swarm gate over fakes that allows the model server `box`, nothing started."""
    built = make_gate_rig(nuc_terms())
    yield built
    await built.close()


async def _colonized(rig: GateRig, **kwargs: object) -> NucDevice:
    """A colonized device whose Warden is dialled in and has declared `box`; nothing is pending."""
    device = await make_nuc_device(rig, "box", wanted=RuntimeLevel.COLONIZED, **kwargs)  # type: ignore[arg-type]
    row = await _row(rig, device)
    assert row.desired_level is None
    return device


async def _row(rig: GateRig, device: NucDevice) -> SwarmNode:
    """The device as the registry holds it now."""
    row = await rig.hive.registry.node(device.node.device_id)
    assert row is not None
    return row


async def _hold_seats(rig: GateRig, held: int) -> ForageGrant:
    """Declare the pool's seats and have a live grant hold `held` of them."""
    ledger = rig.queen_deps.ledger
    await ledger.seats.set_capacity("small", _SEATS)
    grant = make_grant(
        GrantState.ACTIVE, rig.clock, seats=(SeatReservation(source_id="small", seats=held),)
    )
    await ledger.record_grant(grant)
    return grant


async def _release_seats(rig: GateRig, grant: ForageGrant) -> None:
    """End the grant, so the seats it held are free again."""
    await rig.queen_deps.ledger.record_grant(grant.model_copy(update={"state": GrantState.REVOKED}))


async def _finished(rig: GateRig, device: NucDevice) -> None:
    """Wait until no flow owns the device any more."""
    await settle(lambda: not rig.colonizer.busy(device.node.device_id), _FLOW_ROUNDS)


async def _decisions(rig: GateRig) -> list[tuple[str, str]]:
    """Every `queen.decided` as (reason, detail), oldest first."""
    events = await rig.queen_deps.trail.query(TrailQuery(kind="queen.decided"))
    return [(str(e.payload.get("reason")), str(e.payload.get("detail"))) for e in events]


async def _quiet(rig: GateRig, device: NucDevice) -> None:
    """End every task placed on the device and have its Warden report that it only watches."""
    for task in await open_tasks(rig.queen_deps):
        if task.cell_id == device.node.cell_id:
            await rig.queen_deps.chamber.cancel(task.id, "The work ended.")
    report_state(rig, device.node, WardenState.WATCH)


async def test_a_device_is_given_a_model_server_when_the_shared_seats_are_held(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        await run_on(rig, device.node)  # A bee at work, drawing on the shared pool.
        await _hold_seats(rig, 8)  # Eight of the ten seats: 0.8, past the 0.75 threshold.

        launched = await run_pass(rig, make_evaluator(rig))
        await _finished(rig, device)

        assert launched == {device.node.device_id: True}
        assert device.warden.actions == [NucAction.PROMOTE]
        assert (await _row(rig, device)).level is RuntimeLevel.NUC
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "pressure" in str(promoted.payload["reason"])
        await device.close()


async def test_seats_held_under_the_threshold_ask_for_no_server(rig: GateRig) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        await run_on(rig, device.node)
        await _hold_seats(rig, 5)  # Half of them: 0.5.

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {device.node.device_id: False}
        assert device.warden.orders == []
        await device.close()


async def test_a_device_with_no_bee_running_is_not_given_a_server_for_a_strain_it_has_no_part_in(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        await _hold_seats(rig, 10)  # The pool is spent, by Wardens that are not this device's.

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {device.node.device_id: False}
        assert device.warden.orders == []
        await device.close()


async def test_a_task_that_must_survive_a_lost_link_has_its_device_given_a_server(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        await run_on(rig, device.node, survives=True)

        launched = await run_pass(rig, make_evaluator(rig))
        await _finished(rig, device)

        assert launched == {device.node.device_id: True}
        assert (await _row(rig, device)).level is RuntimeLevel.NUC
        [promoted] = await rig.hive.events("swarm.promoted")
        assert "autonomy" in str(promoted.payload["reason"])
        await device.close()


async def test_a_device_the_operator_pinned_at_colonized_is_not_given_a_server(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        await rig.hive.registry.set_ceiling(device.node.device_id, RuntimeLevel.COLONIZED)
        await _hold_seats(rig, 10)
        await run_on(rig, device.node, survives=True)

        launched = await run_pass(rig, make_evaluator(rig))

        assert launched == {device.node.device_id: False}
        assert device.warden.orders == []
        await device.close()


async def test_a_device_that_declared_no_server_is_recorded_as_unable_once(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, wanted=RuntimeLevel.COLONIZED)  # Declares none.
        await run_on(rig, device.node, survives=True)
        evaluator = make_evaluator(rig)

        for _ in range(3):
            assert await run_pass(rig, evaluator) == {device.node.device_id: False}

        assert await _decisions(rig) == [("rung_declined", "no_server")]
        await device.close()


async def test_a_device_with_no_room_for_a_model_is_recorded_as_unable_once(rig: GateRig) -> None:
    async with rig.serving():
        device = await _colonized(rig, free_vram_gib=1.0)  # Neither model fits in a gigabyte.
        await run_on(rig, device.node)
        await _hold_seats(rig, 10)
        evaluator = make_evaluator(rig)

        for _ in range(3):
            assert await run_pass(rig, evaluator) == {device.node.device_id: False}

        assert await _decisions(rig) == [("rung_declined", "vram_too_small")]
        await device.close()


async def test_a_server_that_fails_to_start_is_not_asked_for_again_until_its_wait_has_run_out(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        device.warden.outcomes[NucAction.PROMOTE] = NucOutcome.FAILED
        await run_on(rig, device.node)
        await _hold_seats(rig, 9)
        evaluator = make_evaluator(rig)
        await run_pass(rig, evaluator)
        await _finished(rig, device)

        assert await run_pass(rig, evaluator) == {device.node.device_id: False}  # Backing off.
        assert device.warden.actions.count(NucAction.PROMOTE) == 1

        rig.clock.advance(rig.terms.nuc_retry_after_s)
        assert await run_pass(rig, evaluator) == {device.node.device_id: True}
        await _finished(rig, device)
        assert device.warden.actions.count(NucAction.PROMOTE) == 2
        assert (await _row(rig, device)).level is RuntimeLevel.COLONIZED
        await device.close()


async def test_a_server_the_queen_started_is_stopped_after_its_reasons_have_been_gone_the_wait(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        await run_on(rig, device.node)
        grant = await _hold_seats(rig, 8)
        evaluator = make_evaluator(rig)
        await run_pass(rig, evaluator)
        await _finished(rig, device)
        await run_pass(rig, evaluator)  # The pass after a move settles it: the server is hers.
        await _release_seats(rig, grant)
        await _quiet(rig, device)
        wait = rig.terms.nuc_lower_after_s

        rig.clock.advance(wait - 1)
        assert await run_pass(rig, evaluator) == {device.node.device_id: False}
        rig.clock.advance(1)
        assert await run_pass(rig, evaluator) == {device.node.device_id: True}

        await _finished(rig, device)
        assert device.warden.actions == [NucAction.PROMOTE, NucAction.DEMOTE]
        assert (await _row(rig, device)).level is RuntimeLevel.COLONIZED  # A Nuc, not a gateway.
        [demoted] = await rig.hive.events("swarm.demoted")
        assert "reasons_gone" in str(demoted.payload["reason"])
        await device.close()


async def test_a_server_is_kept_while_the_device_is_at_work_whatever_the_figures_say(
    rig: GateRig,
) -> None:
    async with rig.serving():
        device = await _colonized(rig)
        await run_on(rig, device.node)
        grant = await _hold_seats(rig, 8)
        evaluator = make_evaluator(rig)
        await run_pass(rig, evaluator)
        await _finished(rig, device)
        await run_pass(rig, evaluator)
        await _release_seats(rig, grant)  # The pool is easy again, and long ago.
        report_state(rig, device.node, WardenState.WATCH)
        rig.clock.advance(10 * rig.terms.nuc_lower_after_s)

        assert await run_pass(rig, evaluator) == {device.node.device_id: False}  # A bee runs.
        assert device.warden.actions == [NucAction.PROMOTE]

        await _quiet(rig, device)  # The work ends; only then may the server go.
        assert await run_pass(rig, evaluator) == {device.node.device_id: True}
        await _finished(rig, device)
        assert device.warden.actions == [NucAction.PROMOTE, NucAction.DEMOTE]
        await device.close()


async def test_a_server_the_operator_started_is_never_stopped_by_the_queen(rig: GateRig) -> None:
    async with rig.serving():
        device = await make_nuc_device(rig, "box")  # The operator asked for the Nuc rung.
        assert await rig.colonizer.request(await _row(rig, device))
        await _finished(rig, device)
        assert (await _row(rig, device)).level is RuntimeLevel.NUC
        await _quiet(rig, device)
        rig.clock.advance(100 * rig.terms.nuc_lower_after_s)
        evaluator = make_evaluator(rig)

        assert await run_pass(rig, evaluator) == {device.node.device_id: False}
        assert await run_pass(rig, evaluator) == {device.node.device_id: False}

        assert device.warden.actions == [NucAction.PROMOTE]
        assert (await _row(rig, device)).level is RuntimeLevel.NUC
        await device.close()
