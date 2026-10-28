"""Chaos: a Supersedure whose candidate stalls, and whose old Queen dies mid-move.

Roadmap 13.6's Supersedure half, over two Queens on temp stores (`e2e.supersedure_rig`). The old
Hive's goal waits on a question on the Hive Stand when the human orders the move. A candidate
whose restore never returns (it stopped answering mid-copy) must be rolled back inside the
window, the goal finishing on the old Queen; one killed before it is ready is the plain rollback
of tests/e2e/test_supersedure.py, held to the same bar there. An old Queen
whose process ends mid-move (the copy still running) must refuse to start again until the human
settles the move; once settled as aborted she starts, requeens and the goal finishes. In every
case two Queens are never active at once, and nothing is left open once the Hive is down.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_supersedure.py for the handover and the plain rollback.
    - docs/runbooks/supersedure.md for `hive supersede --settle`.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest
from e2e.chaos.rig import assert_nothing_left_open, bounded
from e2e.kernel_helpers import wait_until
from e2e.supersedure_rig import (
    GOAL,
    HUMAN,
    WAIT_S,
    Candidate,
    answer_on,
    build,
    mode_is,
    recorder,
    sampler,
    setup,
    task_in,
)

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, run_hive
from hivemind.pheromone import TrailQuery
from hivemind.queen.errors import SupersedurePendingError
from hivemind.queen.state import QueenMode
from hivemind.queen.supersedure import ABORTED_KIND, SUPERSEDED_KIND, AbortCause, latest, settle

pytestmark = [pytest.mark.e2e, pytest.mark.chaos]


async def test_a_candidate_that_stalls_is_rolled_back_and_the_goal_finishes(
    tmp_path: Path,
) -> None:
    old, candidate, _sender = await setup(tmp_path, dies=False, stalls=True)
    both: list[tuple[QueenMode, QueenMode]] = []

    finished = await bounded("candidate stalled", _rolled_back(old, candidate, both))

    assert finished.status is TaskStatus.SUCCEEDED
    [aborted] = await old.stores.trail.query(TrailQuery(kind=ABORTED_KIND))
    assert aborted.payload["cause"] == AbortCause.NOT_READY.value
    assert both == [] and candidate.hive is None  # Its Queen never came up to be a second one.
    await assert_nothing_left_open(old)


async def _rolled_back(
    old: Hive, candidate: Candidate, both: list[tuple[QueenMode, QueenMode]]
) -> Task:
    """Order the move mid-goal, wait out the rollback, then answer and finish on the old Queen."""
    async with run_hive(old), sampler(old, candidate, both):
        await old.queen.submit_goal(GOAL, clearance=HoneyClearance.C1)
        blocked = await task_in(old, TaskStatus.BLOCKED)
        await old.queen.supersede(candidate.manifest.hive.node_id, HUMAN)
        await wait_until(lambda: mode_is(old, QueenMode.SUPERSEDING), timeout_s=WAIT_S)
        await wait_until(lambda: mode_is(old, QueenMode.RUNNING), timeout_s=WAIT_S)
        await answer_on(old, blocked)
        return await task_in(old, TaskStatus.SUCCEEDED)


async def test_an_old_queen_killed_mid_move_stays_down_until_settled_then_finishes(
    tmp_path: Path,
) -> None:
    old, candidate, _sender = await setup(tmp_path, stalls=True, dies=False)

    restarted = await bounded("old Queen killed", _killed_then_settled(old, candidate))

    # One start, one outcome, and it is the human's: the move never reached the candidate.
    [aborted] = await restarted.stores.trail.query(TrailQuery(kind=ABORTED_KIND))
    assert aborted.payload["cause"] == AbortCause.SETTLED.value
    assert await restarted.stores.trail.query(TrailQuery(kind=SUPERSEDED_KIND)) == ()
    assert candidate.hive is None
    await assert_nothing_left_open(restarted)


async def _killed_then_settled(old: Hive, candidate: Candidate) -> Hive:
    """End the old Queen's process mid-copy, be refused a start, settle, start and finish."""
    serving = asyncio.ensure_future(_serve_until_superseding(old, candidate))
    await wait_until(lambda: mode_is(old, QueenMode.SUPERSEDING), timeout_s=WAIT_S)
    serving.cancel()  # Her process ends while the copy still runs: no outcome is recorded.
    with contextlib.suppress(asyncio.CancelledError):
        await serving
    record = await latest(old.stores.trail)
    assert record is not None and record.outcome is None
    # A fresh start over the same stores is refused while the move has no outcome.
    with pytest.raises(SupersedurePendingError):
        async with run_hive(await build(old.manifest)):
            pass
    await settle(recorder(old), record, superseded=False, actor=str(HUMAN))
    again = await build(old.manifest)
    async with run_hive(again):
        blocked = await task_in(again, TaskStatus.BLOCKED)
        await answer_on(again, blocked)
        await task_in(again, TaskStatus.SUCCEEDED)
    return again


async def _serve_until_superseding(old: Hive, candidate: Candidate) -> None:
    """Run the old Hive, start a goal and order the move; then keep serving until ended."""
    async with run_hive(old):
        await old.queen.submit_goal(GOAL, clearance=HoneyClearance.C1)
        await task_in(old, TaskStatus.BLOCKED)
        await old.queen.supersede(candidate.manifest.hive.node_id, HUMAN)
        await asyncio.Event().wait()
