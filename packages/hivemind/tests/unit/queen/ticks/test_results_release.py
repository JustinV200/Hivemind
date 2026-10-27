"""Regression: a Virtual Cell is released only once the last task running on it has finished.

Swarming (ADR-0063) places a task on a Virtual Cell already made when it caps fresh ones, so a
Cell runs several tasks at once. The first e2e run found the Cell torn down as soon as the task it
was made for finished, under a second task still RUNNING there, which then never finished.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.results for complete_task and the release it starts.
"""

from __future__ import annotations

import dataclasses

from builders.cells import make_cell
from builders.forage import make_grant
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskOutcome as ChamberOutcome
from hivemind.cell import CellKind
from hivemind.forage.grant_state import GrantState
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import drain_releases
from hivemind.queen.ticks.results import complete_task
from waggle.clock import FakeClock
from waggle.ids import CellId, TaskId
from waggle.messages.labels import HoneyClearance
from waggle.messages.task import TaskOutcome, TaskResult


async def test_a_shared_virtual_cell_is_released_by_its_last_task_only() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, cell=make_cell(kind=CellKind.VIRTUAL))
    released: list[CellId] = []

    async def on_task_finished(cell_id: CellId, outcome: ChamberOutcome) -> None:
        released.append(cell_id)

    deps = dataclasses.replace(deps, on_task_finished=on_task_finished)
    first, second = await _running(deps, link), await _running(deps, link)

    await complete_task(deps, [link], _succeeded(first, link), link.warden_id)
    await drain_releases(deps)
    assert released == []  # The second task still runs there.

    # Its grant ends with it (release_finished, every dispatch pass); then the Cell goes.
    for grant in deps.ledger.live_grants():
        await deps.ledger.record_grant(grant.model_copy(update={"state": GrantState.REVOKED}))
    await complete_task(deps, [link], _succeeded(second, link), link.warden_id)
    await drain_releases(deps)
    assert released == [link.cell.id]
    await warden_end.close()


async def _running(deps: QueenDeps, link: WardenLink) -> TaskId:
    """Start a one-task goal on `link`'s Cell, holding a live grant there."""
    (task,) = await deps.chamber.submit(make_graph_draft({"answer": ()}))
    await deps.chamber.assign(
        task.id, link.warden_id, link.cell.id, "test", bound_tier=link.cell.comb_shield
    )
    await deps.chamber.start(task.id)
    grant = make_grant(
        state=GrantState.ACTIVE,
        clock=deps.clock,
        holder=link.warden_id,
        cell_id=link.cell.id,
        task_id=task.id,
    )
    await deps.ledger.record_grant(grant)
    return task.id


def _succeeded(task_id: TaskId, link: WardenLink) -> TaskResult:
    """The Warden's verified TaskResult(SUCCEEDED) for `task_id`."""
    return TaskResult(
        task_id=task_id,
        attempt=1,
        outcome=TaskOutcome.SUCCEEDED,
        summary="done",
        clearance=HoneyClearance.C1,
        artifacts=(),
        checked_by=link.warden_id,
        handoff=None,
        spend=0.0,
        reason="Every acceptance criterion held.",
    )
