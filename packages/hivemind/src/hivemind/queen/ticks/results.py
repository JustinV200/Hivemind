"""Define complete_task, retry_task and fail_task: what COMPLETE_TASK/RETRY_TASK/FAIL_TASK do.

Roadmap step 3.20's own dispatch map: "TaskResult(SUCCEEDED, checked_by=warden) -> COMPLETE_TASK:
chamber.complete with TaskOutcome(verified_by=checked_by) -> task.succeeded is the chamber's event;
then dispatch_ready picks up dependants" and "...RETRY_TASK (re-dispatch, attempt+1)... FAIL_TASK
(chamber.fail)." These three functions are shared by both triggers a `hivemind.queen.autopilot.
actions.QueenAction` of the same name can have -- a `TaskResult` (`hivemind.queen.ticks.results`'s
own caller) and an escalated `AlarmRaised` (`hivemind.queen.ticks.alarms`'s own caller) both end up
here, since "retry this task" and "fail this task" mean the same chamber calls regardless of which
report triggered the decision. `retry_task` calls `hivemind.queen.dispatcher.redispatch`, never
`chamber.unassign`: the failing task is still RUNNING in the Brood Chamber (its Warden reported the
failure directly, without ever telling the chamber anything happened), and `hivemind.brood_chamber.
task.state.TRANSITIONS` has no edge from RUNNING back to PENDING or ASSIGNED, so "re-dispatch,
attempt+1" is a wire-level re-send to the same placement, not a fresh placement decision -- the
caller (`hivemind.queen.queen`) is what tracks the next attempt number, since the chamber's own
`Task.attempt` field only ever advances through `unassign`. Once a task is complete and a Honey
Store is wired (roadmap phase 7), `complete_task` also deposits its verified outcome as a FINDING
of origin `TASK_OUTCOME` (the task's title, objective and acceptance criteria, the verified
summary, and the Cell it ran on), so what one run learned is there for the next run's pre-check;
that deposit is bounded by a timeout and never raises out of completion.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick dispatch, once per decided
    `COMPLETE_TASK`/`RETRY_TASK`/`FAIL_TASK`. Calls into `hivemind.brood_chamber` (Task,
    TaskOutcome, TaskStatus), `hivemind.cell` (Cell, CombShieldLevel), `hivemind.honey_store`
    (NectarSubmission, NectarOrigin, intake through `QueenDeps.honey`), `hivemind.queen.deps`
    (QueenDeps, WardenLink), `hivemind.queen.dispatcher` (dispatch_ready, redispatch),
    `hivemind.queen.ticks.housekeeping` (placed_cell) and waggle only.

Key invariants:
    - `complete_task` calls `dispatch_ready` immediately afterward, so a dependant task is placed
      without waiting for the next tick.
    - `complete_task` tells `deps.on_task_finished` (roadmap step 5.6/5.9's own release seam, this
      dispatch's own minimal edit) about the reporting Warden's own Cell before `dispatch_ready`
      runs, so a Virtual Cell it releases is never still GRANTED when the next placement decision
      reads `deps.virtual_backends`/`.dormant_cells`.
    - `retry_task` never inspects an attempt ceiling itself, and never changes the task's own
      chamber status: the ceiling decision already happened in
      `hivemind.queen.autopilot.table.decide`; this function only carries out the resend.
    - The outcome deposit never fails or delays a completion past `OUTCOME_DEPOSIT_TIMEOUT_S`: it
      runs after `chamber.complete`, and every failure is logged, never raised. It runs before
      `on_task_finished`, so the Cell's own link (borrowed or not, its tier) is still attached
      when the deposit reads it; a task on a Night Veil Cell deposits nothing at all.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the exact chamber calls this module
      makes.
    - hivemind.queen.autopilot.table for decide, which chooses among these three.
    - hivemind.queen.dispatcher for dispatch_ready and redispatch, the two calls this module makes.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the TASK_OUTCOME origin and its key.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence

from hivemind.brood_chamber import Task, TaskOutcome, TaskStatus
from hivemind.cell import Cell, CombShieldLevel
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.honey_store import NectarOrigin, NectarSubmission
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, redispatch
from hivemind.queen.ticks.housekeeping import placed_cell
from waggle.ids import CellId, TaskId, WardenId
from waggle.messages import Postcondition
from waggle.messages.honey import NectarKind
from waggle.messages.honey.exchange import MAX_TITLE_CHARS
from waggle.messages.task import ArtifactRef, TaskResult

MAX_FAIL_SUMMARY_CHARS = 2_000  # Matches brood_chamber.task.model.MAX_SUMMARY_CHARS's own bound.
# One local SQLite read and one intake write (hash, label, insert): milliseconds normally, so this
# only ever fires on a wedged store, and then completion carries on without the deposit.
OUTCOME_DEPOSIT_TIMEOUT_S = 10.0
OUTCOME_SOURCE_KEY_PREFIX = "task_outcome:"  # ADR-0031's dedupe key for a verified outcome.
OUTCOME_MEDIA_TYPE = "text/markdown"  # The outcome is rendered with a heading and a bullet list.
# A failed deposit is logged and completion carries on: the outcome is durable in the chamber.
_DEPOSIT_FAILURES = (HiveMindError, ValueError, TimeoutError, sqlite3.Error)

__all__ = [
    "MAX_FAIL_SUMMARY_CHARS",
    "OUTCOME_DEPOSIT_TIMEOUT_S",
    "OUTCOME_MEDIA_TYPE",
    "OUTCOME_SOURCE_KEY_PREFIX",
    "complete_task",
    "fail_task",
    "retry_task",
]

log = get_logger(__name__)


async def complete_task(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    payload: TaskResult,
    warden_id: WardenId | None = None,
) -> None:
    """Record a Warden-verified success and place whatever it unblocks.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; dispatch_ready places among these.
        payload: The Warden's own verified TaskResult(SUCCEEDED); `checked_by` becomes
            `TaskOutcome.verified_by`.
        warden_id: The Warden that reported this result, when the caller has it (`hivemind.queen.
            queen`'s own inbox item already names one). Roadmap step 5.6/5.9's own release seam:
            when given and `deps.on_task_finished` is set, that Warden's own Cell is told the task
            finished (`hivemind.queen.cell_gate.release.make_on_task_finished`'s own no-op for a
            Cell the lifecycle does not track -- see `QueenDeps.on_task_finished`'s own docstring).

    With `deps.honey` set, the verified outcome is also deposited into the Honey Store (module
    docstring); that deposit never raises and never waits past `OUTCOME_DEPOSIT_TIMEOUT_S`.
    """
    outcome = TaskOutcome(
        status=TaskStatus.SUCCEEDED,
        summary=payload.summary,
        artifacts=_artifact_paths(payload.artifacts),
        verified_by=payload.checked_by,
        spend_usd=payload.spend,
    )
    await deps.chamber.complete(payload.task_id, outcome)
    # Before on_task_finished: a Virtual Cell it releases takes its link (and so its record of
    # being borrowed or not, and its tier) with it.
    await _deposit_outcome(deps, wardens, payload.task_id, warden_id)
    await _notify_cell_finished(deps, wardens, warden_id, outcome)
    await dispatch_ready(deps, wardens)  # A dependant task may now be ready.


async def _notify_cell_finished(
    deps: QueenDeps, wardens: Sequence[WardenLink], warden_id: WardenId | None, outcome: TaskOutcome
) -> None:
    """Tell `deps.on_task_finished` about `warden_id`'s own Cell, when both are known."""
    if deps.on_task_finished is None or warden_id is None:
        return
    link = next((w for w in wardens if w.warden_id == warden_id), None)
    if link is not None:
        await deps.on_task_finished(link.cell.id, outcome)


async def retry_task(
    deps: QueenDeps, wardens: Sequence[WardenLink], task_id: TaskId, attempt: int
) -> None:
    """Resend a fresh grant and assignment to the same placement, at `attempt`.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; `task_id`'s own Warden must be among these.
        task_id: The task to retry; still RUNNING in the Brood Chamber (module docstring).
        attempt: The next attempt number, tracked by the caller.
    """
    await redispatch(deps, wardens, task_id, attempt)


async def fail_task(deps: QueenDeps, task_id: TaskId, reason: str) -> None:
    """Close a task out as FAILED, for good.

    Args:
        deps: The Queen's collaborators.
        task_id: The task to fail.
        reason: Why it failed, for the outcome's own summary.
    """
    outcome = TaskOutcome(
        status=TaskStatus.FAILED,
        summary=reason[:MAX_FAIL_SUMMARY_CHARS],
        artifacts=(),
        verified_by=None,
        spend_usd=0.0,
    )
    await deps.chamber.fail(task_id, outcome)


def _artifact_paths(artifacts: tuple[ArtifactRef, ...]) -> tuple[str, ...]:
    """Return every artifact's own path; TaskOutcome carries no size or digest of its own."""
    return tuple(artifact.path for artifact in artifacts)


async def _deposit_outcome(
    deps: QueenDeps, wardens: Sequence[WardenLink], task_id: TaskId, warden_id: WardenId | None
) -> None:
    """Deposit a just-completed task's verified outcome as TASK_OUTCOME Nectar; never raises.

    A failure (the store, intake refusing it, the timeout) is logged with its code and the task
    id, and completion carries on: the outcome is already durable in the Brood Chamber.
    """
    if deps.honey is None:
        return  # No Honey Store wired: completion behaves exactly as before phase 7.
    try:
        # Local SQLite reads and one intake write, milliseconds, bounded as a whole.
        async with asyncio.timeout(OUTCOME_DEPOSIT_TIMEOUT_S):
            task = await deps.chamber.get(task_id)
            cell_id, cell = await _where_it_ran(deps, wardens, task_id, warden_id)
            submission = _outcome_submission(task, cell_id, cell)
            if submission is None:
                return  # Never placed, or placed on a Night Veil Cell: nothing to deposit.
            await deps.honey.intake.submit(submission)
    except _DEPOSIT_FAILURES as error:
        code = error.code if isinstance(error, HiveMindError) else type(error).__name__
        log.warning("queen.outcome_deposit_failed", task_id=task_id, reason=code)


async def _where_it_ran(
    deps: QueenDeps, wardens: Sequence[WardenLink], task_id: TaskId, warden_id: WardenId | None
) -> tuple[CellId | None, Cell | None]:
    """Return the Cell a completed task ran on: its id, and its live record when attached.

    The reporting Warden's own Cell is the answer whenever the caller names that Warden; else
    the chamber's record of the task's last placement (completion itself cleared the live one).
    """
    link = next((link for link in wardens if link.warden_id == warden_id), None)
    if link is not None:
        return link.cell.id, link.cell
    cell_id = await placed_cell(deps.chamber, deps.trail, task_id)
    cell = next((link.cell for link in wardens if link.cell.id == cell_id), None)
    return cell_id, cell


def _outcome_submission(
    task: Task, cell_id: CellId | None, cell: Cell | None
) -> NectarSubmission | None:
    """Build a completed task's TASK_OUTCOME submission, or None when it must not be deposited."""
    if cell_id is None or task.outcome is None:
        return None  # A completed task always has both; one without has no outcome to share.
    # A Cell no longer attached is treated as borrowed (the C2 floor): never less sensitive than
    # a Real Cell. Nothing about a Night Veil task is deposited: its outcome is part of the
    # execution record that boundary keeps from outliving the Cell (codingrules section 12).
    if cell is not None and cell.comb_shield is CombShieldLevel.NIGHT_VEIL:
        return None
    return NectarSubmission(
        kind=NectarKind.FINDING,
        origin=NectarOrigin.TASK_OUTCOME,
        media_type=OUTCOME_MEDIA_TYPE,
        title=task.spec.title[:MAX_TITLE_CHARS],
        content=_outcome_text(task, cell_id, cell).encode("utf-8"),
        task_id=task.id,
        cell_id=cell_id,
        observed_at=task.updated_at,
        declared=task.spec.clearance,
        from_borrowed_cell=cell.is_borrowed if cell is not None else True,
        tier=cell.comb_shield if cell is not None else CombShieldLevel.MEADOW,
        source_key=f"{OUTCOME_SOURCE_KEY_PREFIX}{task.id}",
    )


def _outcome_text(task: Task, cell_id: CellId, cell: Cell | None) -> str:
    """Render the outcome as markdown: the verified result first, so a summary keeps it."""
    summary = task.outcome.summary if task.outcome is not None else ""
    where = (
        f"{cell.name} ({cell.capabilities.os.value})"
        if cell is not None
        else f"{cell_id} (no longer attached)"
    )
    lines = [
        f"# {task.spec.title}",
        "",
        f"Verified outcome: {summary}",
        "",
        f"Objective: {task.spec.objective}",
        "",
        "Acceptance criteria (checked by the task's Warden):",
        *(f"- {_criterion(item)}" for item in task.spec.acceptance),
        "",
        f"Cell: {where}",
    ]
    return "\n".join(lines)


def _criterion(item: Postcondition) -> str:
    """Render one acceptance criterion on one line: its kind, subject, command and expectation."""
    text = f"{item.kind.value} {item.subject}"
    if item.argv:
        text += f" (runs: {' '.join(item.argv)})"
    if item.expected is not None:
        text += f" (expects: {item.expected})"
    return text
