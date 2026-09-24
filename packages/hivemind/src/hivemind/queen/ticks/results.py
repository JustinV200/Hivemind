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
`Task.attempt` field only ever advances through `unassign`.

`fail_task`'s own `scout_report` keyword (roadmap step 6.10) is how an infeasible Scout's own
report survives onto the `TaskOutcome` its Warden-verified `TaskResult(SUCCEEDED)` never carried
through `complete_task`: `hivemind.queen.autopilot.table._decide_task_result` maps that exact
combination to `FAIL_TASK` instead of `COMPLETE_TASK`, and `fail_task_from_result` is what a
`TaskResult`-shaped `FAIL_TASK` (`hivemind.queen.queen._act_on_task_result`'s own branch) calls
instead of `fail_task` directly, so the dependents `hivemind.brood_chamber.task.graph.
ready_tasks` gates on it are never dispatched, without a second non-retryable state this module
would have to add; it then cancels them, with the Scout's reason, so the goal ends rather than
waiting on tasks that can never become ready.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick dispatch, once per decided
    `COMPLETE_TASK`/`RETRY_TASK`/`FAIL_TASK`. Calls into `hivemind.brood_chamber` (TaskOutcome,
    TaskStatus), `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.dispatcher`
    (dispatch_ready, redispatch) and waggle (including `waggle.messages.task.ScoutReport`) only.

Key invariants:
    - `complete_task` calls `dispatch_ready` immediately afterward, so a dependant task is placed
      without waiting for the next tick.
    - `complete_task` carries `payload.scout_report` onto the SUCCEEDED `TaskOutcome` unchanged
      (roadmap step 6.10); an infeasible one never reaches it, since `hivemind.queen.autopilot.
      table` routes that case to FAIL_TASK before `complete_task` is ever called.
    - `complete_task` tells `deps.on_task_finished` (roadmap step 5.6/5.9's own release seam, this
      dispatch's own minimal edit) about the reporting Warden's own Cell before `dispatch_ready`
      runs, so a Virtual Cell it releases is never still GRANTED when the next placement decision
      reads `deps.virtual_backends`/`.dormant_cells`.
    - `retry_task` never inspects an attempt ceiling itself, and never changes the task's own
      chamber status: the ceiling decision already happened in
      `hivemind.queen.autopilot.table.decide`; this function only carries out the resend.
    - `fail_task` never dispatches anything and never retries: it is the one caller of
      `chamber.fail`, whatever put it on the FAIL_TASK path (an exhausted retry, an escalated
      Alarm's own CANCEL, or an infeasible Scout).
    - An infeasible Scout's transitive dependents are CANCELLED with its reason, so its goal ends;
      any other failed task's dependents are left PENDING, as before.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the exact chamber calls this module
      makes.
    - .claude/roadmap.md step 6.10 for the Scout role and its infeasible-report path.
    - hivemind.queen.autopilot.table for decide, which chooses among these three.
    - hivemind.queen.dispatcher for dispatch_ready and redispatch, the two calls this module makes.
    - hivemind.queen.queen for _act_on_task_result, the one caller of fail_task_from_result.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import TaskFilter, TaskOutcome, TaskStatus, descendants, is_terminal
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, redispatch
from waggle.ids import TaskId, WardenId
from waggle.messages.task import ArtifactRef, ScoutReport, TaskResult
from waggle.messages.task import TaskOutcome as WireTaskOutcome

MAX_FAIL_SUMMARY_CHARS = 2_000  # Matches brood_chamber.task.model.MAX_SUMMARY_CHARS's own bound.

__all__ = [
    "MAX_FAIL_SUMMARY_CHARS",
    "complete_task",
    "fail_reason",
    "fail_task",
    "fail_task_from_result",
    "retry_task",
]


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
            `TaskOutcome.verified_by` and `scout_report` (roadmap step 6.10) carries straight
            onto `TaskOutcome.scout_report` unchanged.
        warden_id: The Warden that reported this result, when the caller has it (`hivemind.queen.
            queen`'s own inbox item already names one). Roadmap step 5.6/5.9's own release seam:
            when given and `deps.on_task_finished` is set, that Warden's own Cell is told the task
            finished (`hivemind.queen.cell_gate.release.make_on_task_finished`'s own no-op for a
            Cell the lifecycle does not track -- see `QueenDeps.on_task_finished`'s own docstring).
    """
    outcome = TaskOutcome(
        status=TaskStatus.SUCCEEDED,
        summary=payload.summary,
        artifacts=_artifact_paths(payload.artifacts),
        verified_by=payload.checked_by,
        spend_usd=payload.spend,
        # Roadmap step 6.10: a feasible Scout's own report, carried unchanged; None for every
        # other role. An infeasible one never reaches here (hivemind.queen.autopilot.table
        # routes it to FAIL_TASK/fail_task_from_result instead, before complete_task is called).
        scout_report=payload.scout_report,
    )
    await deps.chamber.complete(payload.task_id, outcome)
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


def fail_reason(payload: TaskResult) -> str:
    """Build FAIL_TASK's own reason: an infeasible Scout's summary, or the result's own reason.

    Roadmap step 6.10: `hivemind.queen.autopilot.table._decide_task_result` is the only path
    that reaches FAIL_TASK from a SUCCEEDED result, and it does so exactly when
    `payload.scout_report` is present and infeasible -- every other FAIL_TASK (attempts
    exhausted, or an escalated Alarm's own CANCEL, `hivemind.queen.ticks.alarms`) already carries
    its own failure cause on `payload.reason`. `fail_task` caps whichever string this returns, so
    no cap is needed here.

    Args:
        payload: The TaskResult a FAIL_TASK decision was made for.

    Returns:
        The reason to record on the failed task's own outcome.
    """
    report = payload.scout_report
    if _is_infeasible_scout(payload) and report is not None:
        return f"Scout reported the work infeasible: {report.summary}"
    return payload.reason


async def fail_task(
    deps: QueenDeps, task_id: TaskId, reason: str, *, scout_report: ScoutReport | None = None
) -> None:
    """Close a task out as FAILED, for good.

    Args:
        deps: The Queen's collaborators.
        task_id: The task to fail.
        reason: Why it failed, for the outcome's own summary.
        scout_report: The Scout's own report, when this FAILED outcome is an infeasible Scout
            (roadmap step 6.10; module docstring); None for every other reason this is called.
    """
    outcome = TaskOutcome(
        status=TaskStatus.FAILED,
        summary=reason[:MAX_FAIL_SUMMARY_CHARS],
        artifacts=(),
        verified_by=None,
        spend_usd=0.0,
        scout_report=scout_report,
    )
    await deps.chamber.fail(task_id, outcome)


async def fail_task_from_result(deps: QueenDeps, payload: TaskResult) -> None:
    """Fail `payload`'s own task, building its reason and carrying its report in one call.

    The one caller (`hivemind.queen.queen._act_on_task_result`, its own FAIL_TASK branch) never
    has to know `fail_reason`'s own rule or that `fail_task` takes a `scout_report` at all. An
    infeasible Scout also cancels every task that depends on it, directly or not, with the Scout's
    reason: the Scout said the work should not go ahead, so those tasks can never become ready,
    and cancelling them lets the goal end now instead of sitting PENDING until a caller's timeout.

    Args:
        deps: The Queen's collaborators.
        payload: The TaskResult a FAIL_TASK decision was made for.
    """
    reason = fail_reason(payload)
    await fail_task(deps, payload.task_id, reason, scout_report=payload.scout_report)
    if _is_infeasible_scout(payload):
        await _cancel_held_back(deps, payload.task_id, reason)


def _is_infeasible_scout(payload: TaskResult) -> bool:
    """Return whether `payload` is a Scout's verified result recommending against the work."""
    report = payload.scout_report
    return (
        payload.outcome is WireTaskOutcome.SUCCEEDED and report is not None and not report.feasible
    )


async def _cancel_held_back(deps: QueenDeps, scout_id: TaskId, reason: str) -> None:
    """Cancel every unfinished task that depends on the infeasible Scout `scout_id`."""
    scout = await deps.chamber.get(scout_id)
    goal_tasks = await deps.chamber.list(TaskFilter(goal_id=scout.goal_id))
    by_id = {task.id: task for task in goal_tasks}
    held = f"Held back by Scout {scout_id}. {reason}"[:MAX_FAIL_SUMMARY_CHARS]
    # Every transitive dependent, never the Scout's siblings: only work that waited on its
    # findings is held back. One already finished (or cancelled) is left as it ended.
    for task_id in sorted(descendants(goal_tasks, scout_id)):
        if not is_terminal(by_id[task_id].status):
            await deps.chamber.cancel(task_id, held)


def _artifact_paths(artifacts: tuple[ArtifactRef, ...]) -> tuple[str, ...]:
    """Return every artifact's own path; TaskOutcome carries no size or digest of its own."""
    return tuple(artifact.path for artifact in artifacts)
