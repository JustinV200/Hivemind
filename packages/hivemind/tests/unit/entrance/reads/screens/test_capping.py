"""Test hivemind.entrance.reads.screens.capping: the Capping view read from the trail.

A Hive that proposed nothing reads an empty view; the capping events on the trail fold into
proposals by state.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/capping.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.screens import scene
from builders.observation import after, make_event

from hivemind.entrance.reads.screens import read_capping
from hivemind.supervision.capping import ProposalState
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_message_id, new_task_id


async def test_a_hive_that_proposed_nothing_reads_an_empty_view() -> None:
    view = await read_capping(scene(FakeClock()).reads)

    assert (view.in_flight, view.finished, view.audits) == ([], [], [])


async def test_capping_events_fold_into_proposals_by_state() -> None:
    clock = FakeClock()
    hive = scene(clock)
    proposal = new_message_id(clock)
    payload = {"task_id": new_task_id(clock), "cell_id": new_cell_id(clock), "tier": "READ_ONLY"}
    await hive.record(make_event("capping.proposed", proposal, after(1), payload=payload))
    await hive.record(
        make_event("capping.verified", proposal, after(2), payload={"postconditions_held": 1})
    )

    view = await read_capping(hive.reads)

    [row] = view.finished
    assert (row.id, row.state, row.tier) == (proposal, ProposalState.VERIFIED, "READ_ONLY")
