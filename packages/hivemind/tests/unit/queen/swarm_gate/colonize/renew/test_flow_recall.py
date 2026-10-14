"""Tests for a renewal's resume: the task goes back to the Handoff its bee deposited.

A colonized device's Warden is restarted when the operator changes its terms, and every task it
supervises is checkpointed and resumed on the new Warden (ADR-0053). The bee's checkpoint is written
in the device's own store, so the Queen's memory holds at most the Handoff the colonization carried,
and the newest she can hold is the HANDOFF Nectar the bee deposited. The renewal used to read only
her memory and so sent the new Warden back to the older Handoff, work redone for nothing; these
tests run the whole flow with her store empty and the deposit in the Honey Store, and read the
assignment the new Warden was sent.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/renew/flow.py (codingrules section 3), split by
    feature (14.2) from test_flow.py. The recall's own rules are held in
    tests/unit/queen/swarm_gate/test_recall.py and its use by a move in ../test_steps_recall.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.renew.flow for the module under test.
    - hivemind.queen.swarm_gate.recall for the Handoff it asks for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.memory import make_handoff
from builders.quarantine import settle
from builders.recall import Refusal, Site, make_deposits
from builders.swarm import GateRig, make_renewal

from hivemind.cell import AccessLevel
from waggle.messages.task import TaskAssign


async def _renewed_assignment(
    rig: GateRig, tmp_path: Path, plant: Refusal | str | None
) -> TaskAssign:
    """Renew a device with `plant` in the Honey Store; the assignment its new Warden is sent.

    Args:
        rig: The renewal rig, not yet serving.
        tmp_path: A writable directory for the Honey Store.
        plant: A `Refusal` to plant, a progress line for a Handoff the bee deposited, or None for
            nothing at all.
    """
    deposits = await make_deposits(rig.queen_deps, rig.clock, tmp_path)
    rig.queen_deps = deposits.deps
    async with rig.serving():
        scenario = await make_renewal(rig)
        node = scenario.device.node
        site = Site(node.warden_id, node.cell_id, scenario.task.id)
        if isinstance(plant, Refusal):
            await deposits.refused(site, plant)
        elif plant is not None:
            await deposits.deposit(site, make_handoff(rig.clock, progress=plant))
        await scenario.narrow(AccessLevel.SCRATCH)

        assert await scenario.renew()
        await scenario.finish()

        await settle(lambda: bool(scenario.started[0].assignments))
        [assignment] = scenario.started[0].assignments
        await scenario.close()
        return assignment


async def test_the_new_warden_is_sent_back_to_the_handoff_the_bee_deposited(
    rig: GateRig, tmp_path: Path
) -> None:
    assignment = await _renewed_assignment(rig, tmp_path, "On the device.")

    # Her own store held nothing, so the Handoff the Warden is told to resume from, and sent inline
    # because it cannot read her store, is the one she recalled from the deposit.
    assert assignment.resume_from is not None
    assert assignment.handoff is not None and assignment.handoff.progress == "On the device."


@pytest.mark.parametrize("case", list(Refusal), ids=lambda case: case.value)
async def test_a_handoff_a_recall_refuses_is_not_sent_to_the_new_warden(
    rig: GateRig, tmp_path: Path, case: Refusal
) -> None:
    assignment = await _renewed_assignment(rig, tmp_path, case)

    # The task is resumed all the same, from nothing: a refusal is never an error.
    assert assignment.resume_from is None and assignment.handoff is None


async def test_a_task_whose_bee_deposited_nothing_resumes_as_it_always_did(
    rig: GateRig, tmp_path: Path
) -> None:
    assignment = await _renewed_assignment(rig, tmp_path, None)

    assert assignment.resume_from is None and assignment.handoff is None
