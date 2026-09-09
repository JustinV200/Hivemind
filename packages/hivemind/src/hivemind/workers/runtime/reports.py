"""Build the wire messages a WorkerRuntime sends: Heartbeat, TaskProgress, TaskResult, AlarmRaised.

Pure functions (codingrules section 8.3: "pure core, effectful edges") that turn a
`WorkerContext`, the active `TaskAssign` and a small request bundle into the exact waggle message
`hivemind.workers.runtime.mailbox.Mailbox.send` then wraps and sends; none of them touches the
transport, the trail or the clock beyond what `ctx.clock` and `ctx.telemetry` already carry.
`ResultDetails` and `AlarmDetails` exist because `TaskResult` and `AlarmRaised` each carry more
independently varying fields than codingrules 5.1's parameter limit allows on one function
signature; grouping them is that section's own named remedy ("introduce a frozen dataclass for
the argument group"). `checked_by` on a built `TaskResult` is always `None`: a Worker's own hop
may only claim (`waggle.messages.task.reports.TaskResult`'s own docstring), never report a
Warden-verified outcome.

Fits into the Hive:
    Layer 4 (roles that do the work). Called only by `hivemind.workers.runtime.loop.WorkerRuntime`
    (roadmap step 3.15). Calls into `hivemind.workers.context`, `hivemind.workers.state` and
    waggle only.

Key invariants:
    - Every free-text field passed through here is truncated to the wire message's own bound
      (`MAX_SUMMARY_CHARS`, `MAX_REASON_CHARS`, `MAX_DETAIL_CHARS`) before the pydantic model
      itself validates it, so a role's own text can never fail a report outright.
    - `build_result`'s `checked_by` is always None; only a Warden ever sets it.

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit `ResultDetails` and
      `AlarmDetails` exist to keep.
    - .claude/codingrules.md section 8.3 for the pure-core rule this module follows.
    - waggle.messages.task.reports for TaskProgress, TaskResult, TaskStage, TaskOutcome and their
      own field bounds.
    - waggle.messages.supervision for Heartbeat and AlarmRaised, and their own field bounds.
    - hivemind.workers.runtime.mailbox for Mailbox.send, which wraps and sends what this module
      builds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hivemind.workers.context import WorkerContext
from hivemind.workers.state import WorkerState
from waggle.ids import TaskId, new_alarm_id
from waggle.messages import AlarmSeverity, HandoffRef
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised, Heartbeat
from waggle.messages.supervision.alarms import MAX_DETAIL_CHARS
from waggle.messages.task import (
    ArtifactRef,
    TaskAssign,
    TaskOutcome,
    TaskProgress,
    TaskResult,
    TaskStage,
)
from waggle.messages.task.reports import MAX_SUMMARY_CHARS

__all__ = [
    "AlarmDetails",
    "ResultDetails",
    "build_alarm",
    "build_heartbeat",
    "build_progress",
    "build_result",
]


@dataclass(frozen=True, slots=True)
class ResultDetails:
    """The fields of a TaskResult that vary per outcome, grouped to stay within codingrules 5.1."""

    outcome: TaskOutcome  # CLAIMED, or CANCELLED/FAILED once this attempt is over.
    summary: str  # One paragraph of what was done; truncated to MAX_SUMMARY_CHARS.
    reason: str  # Why this outcome; truncated to MAX_REASON_CHARS.
    artifacts: tuple[ArtifactRef, ...] = ()  # Outputs produced, if any.
    handoff: HandoffRef | None = None  # The last Handoff written, for a retry to resume from.
    spend: float = 0.0  # Total spend charged to this attempt.


@dataclass(frozen=True, slots=True)
class AlarmDetails:
    """The fields of an AlarmRaised that vary per Alarm, grouped to stay within codingrules 5.1."""

    kind: AlarmKind  # What went wrong, as the escalation policy keys it.
    detail: str  # The failing assertion, error or observation; truncated to MAX_DETAIL_CHARS.
    reason: str  # Why the Worker escalates rather than handling it; truncated to MAX_REASON_CHARS.
    severity: AlarmSeverity = field(default=AlarmSeverity.WARNING)  # How bad; WARNING by default.


def build_heartbeat(
    ctx: WorkerContext, state: WorkerState, task_id: TaskId | None, interval_s: float
) -> Heartbeat:
    """Build the Heartbeat this Worker sends on its own cadence.

    Args:
        ctx: This Worker's context; `telemetry` and `grant` supply the beat's own figures.
        state: This Worker's current WorkerState.
        task_id: The task this Worker is currently attempting, or None while idle.
        interval_s: This runtime's own heartbeat cadence, so the receiver's watchdog can size its
            timeout.

    Returns:
        A Heartbeat with no children (a Worker reports only its own telemetry, never a sub-bee's)
        and `grant_spend` read straight from the current telemetry snapshot's own `spend`.
    """
    telemetry = ctx.telemetry.snapshot()
    return Heartbeat(
        telemetry=telemetry,
        task_id=task_id,
        worker_state=state.to_wire(),
        warden_state=None,
        children=(),
        grant_id=ctx.grant.grant_id,
        grant_spend=telemetry.spend,
        interval_s=interval_s,
    )


def build_progress(
    assignment: TaskAssign, stage: TaskStage, summary: str, handoff: HandoffRef | None = None
) -> TaskProgress:
    """Build a TaskProgress reporting `stage`, echoing `assignment`'s own task id and attempt.

    Args:
        assignment: The TaskAssign this attempt is answering; supplies `task_id`, `attempt` and
            `clearance`.
        stage: What happened.
        summary: One paragraph of what changed; truncated to MAX_SUMMARY_CHARS.
        handoff: The Handoff just written; required by TaskProgress's own validator exactly when
            `stage` is CHECKPOINTED, forbidden otherwise.

    Returns:
        A validated TaskProgress.
    """
    return TaskProgress(
        task_id=assignment.task_id,
        attempt=assignment.attempt,
        stage=stage,
        summary=summary[:MAX_SUMMARY_CHARS],
        clearance=assignment.clearance,
        fraction_done=None,  # This runtime does not estimate completion; a role's own tools may.
        handoff=handoff,
    )


def build_result(assignment: TaskAssign, details: ResultDetails) -> TaskResult:
    """Build a TaskResult closing this attempt, echoing `assignment`'s own task id and attempt.

    Args:
        assignment: The TaskAssign this attempt is answering; supplies `task_id`, `attempt` and
            `clearance`.
        details: The fields that vary per outcome (see ResultDetails).

    Returns:
        A validated TaskResult with `checked_by=None`: a Worker's own hop may only claim, never
        report a Warden-verified outcome.
    """
    return TaskResult(
        task_id=assignment.task_id,
        attempt=assignment.attempt,
        outcome=details.outcome,
        summary=details.summary[:MAX_SUMMARY_CHARS],
        clearance=assignment.clearance,
        artifacts=details.artifacts,
        checked_by=None,
        handoff=details.handoff,
        spend=details.spend,
        reason=details.reason[:MAX_REASON_CHARS],
    )


def build_alarm(ctx: WorkerContext, assignment: TaskAssign, details: AlarmDetails) -> AlarmRaised:
    """Build an AlarmRaised escalating an issue this Worker cannot resolve itself.

    Args:
        ctx: This Worker's context; supplies `worker_id`, `cell.id` and `clock`.
        assignment: The TaskAssign this attempt is answering; supplies `task_id` and `clearance`.
        details: The fields that vary per Alarm (see AlarmDetails).

    Returns:
        A validated AlarmRaised at attempt 0 (the raising hop; each level above increments it),
        raised now.
    """
    context = AlarmContext(
        task_id=assignment.task_id,
        cell_id=ctx.cell.id,
        worker_id=ctx.worker_id,
        event_id=None,
        handoff=None,
    )
    return AlarmRaised(
        alarm_id=new_alarm_id(ctx.clock),
        kind=details.kind,
        severity=details.severity,
        origin=ctx.worker_id,
        attempts=0,
        raised_at=ctx.clock.now(),
        context=context,
        detail=details.detail[:MAX_DETAIL_CHARS],
        clearance=assignment.clearance,
        reason=details.reason[:MAX_REASON_CHARS],
    )
