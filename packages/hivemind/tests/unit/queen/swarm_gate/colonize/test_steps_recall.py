"""Tests for hivemind.queen.swarm_gate.colonize.steps.pause_for_move: the Handoff it notes.

A Warden that is moved or renewed has each of its bees write a Handoff and stop, and the task
resumes from the Handoff `pause_for_move` noted. For a colonized device's Warden the bees wrote
theirs in the device's own store, so the Queen's memory holds at most the one the colonization
carried, and the newest she can hold is the HANDOFF Nectar the bee deposited (ADR-0053). Every test
here starts with her store empty (or holding only an older checkpoint) and the Honey Store holding
the deposit, which is how a defect that read only her own store (a renewal resuming from an older
Handoff) hid from tests that seeded it.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/steps.py (codingrules section 3); the orders
    and the plain pause are in test_steps.py (split by feature, 14.2). A device is a Warden link
    attached to a real Queen whose far end the test reads; a task is really RUNNING on it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.steps for the module under test.
    - tests.unit.queen.swarm_gate.test_recall for the recall's own rules, held to a taken-back task.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.memory import make_handoff
from builders.recall import Refusal, Site, make_deposits
from builders.swarm import GateRig, place_on_device

from hivemind.brood_chamber import TaskStatus
from hivemind.queen.swarm_gate import GateContext
from hivemind.queen.swarm_gate.colonize.steps import Move, pause_for_move
from hivemind.swarm import RuntimeLevel


async def test_a_move_notes_the_handoff_its_bee_deposited_when_her_own_store_holds_none(
    rig: GateRig, tmp_path: Path
) -> None:
    deposits = await make_deposits(rig.queen_deps, rig.clock, tmp_path)
    rig.queen_deps = deposits.deps
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        site = Site(placed.node.warden_id, placed.node.cell_id, placed.task.id)
        deposited = await deposits.deposit(site, make_handoff(rig.clock, progress="On the device."))
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, deposits.deps), move, "Renewing.")

        # What she noted she filed as a checkpoint of her own (the device's event stays the
        # device's), so the task's next placement, and the copy the new Warden is sent, read it.
        ref = move.refs[placed.task.id]
        assert ref is not None and ref.event_id != deposited.event_id
        assert (await deposits.held(ref)).progress == "On the device."
        assert (await rig.queen_deps.chamber.get(placed.task.id)).status is TaskStatus.PAUSED
        [decision] = await deposits.decided("handoff_recalled")
        assert decision["source_event_id"] == deposited.event_id


async def test_a_deposit_newer_than_the_handoff_the_colonization_carried_is_the_one_noted(
    rig: GateRig, tmp_path: Path
) -> None:
    deposits = await make_deposits(rig.queen_deps, rig.clock, tmp_path)
    rig.queen_deps = deposits.deps
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        site = Site(placed.node.warden_id, placed.node.cell_id, placed.task.id)
        carried = await deposits.hold(placed.task.id, make_handoff(rig.clock, progress="Moved."))
        rig.clock.advance(60)  # The bee went on after the Handoff the move carried onto the device.
        await deposits.deposit(site, make_handoff(rig.clock, progress="On the device."))
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, deposits.deps), move, "Renewing.")

        ref = move.refs[placed.task.id]
        assert ref is not None and ref != carried  # Not the older one she held.
        assert (await deposits.held(ref)).progress == "On the device."


async def test_the_handoff_she_holds_is_noted_when_no_deposit_is_newer(
    rig: GateRig, tmp_path: Path
) -> None:
    deposits = await make_deposits(rig.queen_deps, rig.clock, tmp_path)
    rig.queen_deps = deposits.deps
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        carried = await deposits.hold(placed.task.id, make_handoff(rig.clock, progress="Held."))
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, deposits.deps), move, "Colonizing.")

        assert move.refs == {placed.task.id: carried}  # A Level 0 device's: nothing is recalled.
        assert await deposits.decided("handoff_recalled") == []


@pytest.mark.parametrize("case", list(Refusal), ids=lambda case: case.value)
async def test_a_move_notes_no_handoff_that_a_recall_refuses(
    rig: GateRig, tmp_path: Path, case: Refusal
) -> None:
    deposits = await make_deposits(rig.queen_deps, rig.clock, tmp_path)
    rig.queen_deps = deposits.deps
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        await deposits.refused(
            Site(placed.node.warden_id, placed.node.cell_id, placed.task.id), case
        )
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, deposits.deps), move, "Renewing.")

        # The task still pauses and still resumes, from nothing: a refusal is never an error.
        assert move.paused == (placed.task.id,)
        assert move.refs == {placed.task.id: None}
        refusals = [row["detail"] for row in await deposits.decided("handoff_not_recalled")]
        assert refusals == ([case.recorded] if case.recorded is not None else [])


async def test_a_queen_with_no_honey_store_notes_what_she_holds(rig: GateRig) -> None:
    async with rig.serving():
        placed = await place_on_device(rig, RuntimeLevel.COLONIZED)
        deps = rig.queen_deps  # The rig's own: no Honey Store is wired.
        assert deps.honey is None
        move = Move(placed.node, placed.device.link)

        await pause_for_move(GateContext(rig.queen, deps), move, "Renewing.")

        assert move.refs == {placed.task.id: None}
