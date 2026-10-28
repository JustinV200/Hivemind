"""End to end: two Queens on temp stores, one Supersedure handed over and one rolled back.

Roadmap 13.2a's proof in one process (ADR-0065). The old Hive runs a goal whose Drone waits on a
question on the Hive Stand. The human's order reaches `Queen.supersede` (the point, the preflight,
the freeze, the start), and the rest runs on her loop. The candidate is a second Hive over its own
directory with the same Hive id and key and its own node: the archive is checked and restored
there the way `hive restore` does it, and its Queen comes up REQUEENING and held. A sampler
watches both Queens the whole time and fails if both are ever active. Once handed over, the old
Queen is SUPERSEDED, the candidate places the goal's task afresh, asks again, and finishes it.
In the second test the candidate dies before it is ready; the old Queen rolls back inside the
window, points the devices back at herself, and the goal still finishes on her.

The real exit (supersede onto an enrolled Nuc while a goal runs on the Hive Stand, a container
and the Nuc) needs that hardware; it is the operator's run (docs/runbooks/supersedure.md).

Fits into the Hive:
    Whole-Hive scenario (codingrules 14.2, `e2e`). Exercises hivemind.queen.supersedure through
    the Queen's door, hivemind.cli.compose.hive.supersedure (the start rules and the hold) and
    Requeening's held first half.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/queen/supersedure for each piece over fakes.
    - tests/e2e/supersedure_rig.py for the two Hives and their reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from e2e.kernel_helpers import wait_until
from e2e.supersedure_rig import (
    GOAL,
    HUMAN,
    WAIT_S,
    answer_on,
    mode_is,
    sampler,
    setup,
    started,
    task_in,
)

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import run_hive
from hivemind.pheromone import TrailQuery
from hivemind.queen.state import QueenMode
from hivemind.queen.supersedure import ABORTED_KIND, STARTED_KIND, SUPERSEDED_KIND

pytestmark = pytest.mark.e2e


async def test_a_ready_candidate_takes_the_hive_and_two_queens_never_run(tmp_path: Path) -> None:
    old, candidate, sender = await setup(tmp_path, dies=False)
    active_both: list[tuple[QueenMode, QueenMode]] = []
    try:
        async with run_hive(old), sampler(old, candidate, active_both):
            await old.queen.submit_goal(GOAL, clearance=HoneyClearance.C1)
            blocked = await task_in(old, TaskStatus.BLOCKED)
            await old.queen.supersede(candidate.manifest.hive.node_id, HUMAN)
            await wait_until(lambda: mode_is(old, QueenMode.SUPERSEDED), timeout_s=WAIT_S)
            new = await started(candidate)
            await wait_until(lambda: mode_is(new, QueenMode.RUNNING), timeout_s=WAIT_S)
            await answer_on(new, blocked)
            finished = await task_in(new, TaskStatus.SUCCEEDED)
    finally:
        await candidate.stop()

    assert active_both == []
    assert finished.id == blocked.id
    for hive in (old, new):
        assert len(await hive.stores.trail.query(TrailQuery(kind=SUPERSEDED_KIND))) == 1
        assert len(await hive.stores.trail.query(TrailQuery(kind=STARTED_KIND))) == 1
    [notice] = sender.notices
    assert notice.new_node_id == candidate.manifest.hive.node_id and not notice.is_rollback


async def test_a_candidate_killed_before_it_is_ready_is_rolled_back_and_the_goal_finishes(
    tmp_path: Path,
) -> None:
    old, candidate, sender = await setup(tmp_path, dies=True)
    async with run_hive(old):
        await old.queen.submit_goal(GOAL, clearance=HoneyClearance.C1)
        blocked = await task_in(old, TaskStatus.BLOCKED)
        await old.queen.supersede(candidate.manifest.hive.node_id, HUMAN)
        await wait_until(lambda: mode_is(old, QueenMode.RUNNING), timeout_s=WAIT_S)
        await answer_on(old, blocked)
        finished = await task_in(old, TaskStatus.SUCCEEDED)

    assert finished.id == blocked.id
    [aborted] = await old.stores.trail.query(TrailQuery(kind=ABORTED_KIND))
    assert aborted.payload["cause"] == "NOT_READY"
    assert await old.stores.trail.query(TrailQuery(kind=SUPERSEDED_KIND)) == ()
    [notice] = sender.notices
    assert notice.is_rollback and notice.new_node_id == old.manifest.hive.node_id
