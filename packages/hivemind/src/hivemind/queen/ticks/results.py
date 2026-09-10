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

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick dispatch, once per decided
    `COMPLETE_TASK`/`RETRY_TASK`/`FAIL_TASK`. Calls into `hivemind.brood_chamber` (TaskOutcome,
    TaskStatus), `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.dispatcher`
    (dispatch_ready, redispatch) and waggle only.

Key invariants:
    - `complete_task` calls `dispatch_ready` immediately afterward, so a dependant task is placed
      without waiting for the next tick.
    - `retry_task` never inspects an attempt ceiling itself, and never changes the task's own
      chamber status: the ceiling decision already happened in
      `hivemind.queen.autopilot.table.decide`; this function only carries out the resend.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the exact chamber calls this module
      makes.
    - hivemind.queen.autopilot.table for decide, which chooses among these three.
    - hivemind.queen.dispatcher for dispatch_ready and redispatch, the two calls this module makes.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import TaskOutcome, TaskStatus
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, redispatch
from waggle.ids import TaskId
from waggle.messages.task import ArtifactRef, TaskResult

MAX_FAIL_SUMMARY_CHARS = 2_000  # Matches brood_chamber.task.model.MAX_SUMMARY_CHARS's own bound.

__all__ = ["MAX_FAIL_SUMMARY_CHARS", "complete_task", "fail_task", "retry_task"]


async def complete_task(
    deps: QueenDeps, wardens: Sequence[WardenLink], payload: TaskResult
) -> None:
    """Record a Warden-verified success and place whatever it unblocks.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; dispatch_ready places among these.
        payload: The Warden's own verified TaskResult(SUCCEEDED); `checked_by` becomes
            `TaskOutcome.verified_by`.
    """
    outcome = TaskOutcome(
        status=TaskStatus.SUCCEEDED,
        summary=payload.summary,
        artifacts=_artifact_paths(payload.artifacts),
        verified_by=payload.checked_by,
        spend_usd=payload.spend,
    )
    await deps.chamber.complete(payload.task_id, outcome)
    await dispatch_ready(deps, wardens)  # A dependant task may now be ready.


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
