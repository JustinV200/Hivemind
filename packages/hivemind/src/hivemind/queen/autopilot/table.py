"""Define decide: the Queen's deterministic dispatch table over one inbox item.

Codingrules section 8.8: "Autopilot returns an action or NEEDS_JUDGEMENT; only the latter runs an
awake episode." `decide` is that table for the Queen: given one `hivemind.supervision.attendant.
InboxItem` (already scored and ordered by the Attendant), the `hivemind.brood_chamber.Task` it
concerns (when one exists), how many attempts have already been made against it, the Queen's own
`EscalationPolicy` and her attempt ceiling, it returns exactly one
`hivemind.queen.autopilot.actions.QueenAction`. The table is keyed by the wrapped payload's own
type: a `Heartbeat` is `RECORD`; a `TaskResult` resolves to `COMPLETE_TASK`, `RETRY_TASK` or
`FAIL_TASK`; an `AlarmRaised` is mapped through `hivemind.supervision.policy.decide`, exactly the
way `hivemind.wardens.autopilot.table._decide_alarm` does for a Warden, but capped by `limit`
first, since the Queen is the last supervisor before the human and must never retry or rebind
forever; a `Question` is `BLOCK_ON_QUESTION`; an `Answer` is `ROUTE_ANSWER`; anything this table
has never seen returns `NEEDS_JUDGEMENT`. A task already in a terminal `TaskStatus`
(`hivemind.brood_chamber.TERMINAL_STATUSES`) makes a `TaskResult`/`AlarmRaised` about it a no-op
`RECORD`: a stale or duplicate report about work the Queen already closed out is not a fresh
decision to make.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    autopilot sub-package (which never imports `hivemind.llm`). Called once per ordered inbox item
    by `hivemind.queen.queen.Queen`'s tick. Calls into `hivemind.brood_chamber` (Task, TaskStatus,
    TERMINAL_STATUSES), `hivemind.supervision` (Alarm, EscalationPolicy, PolicyAction, decide),
    `hivemind.supervision.attendant` (InboxItem) and waggle only.

Key invariants:
    - This module imports no `hivemind.llm`, directly or transitively (codingrules section 4;
      `lint-imports` enforces it for every module under an `autopilot/` directory).
    - `decide` is pure: given the same `(item, task, attempts, policy, limit)`, it always returns
      the same `QueenAction`; it reads nothing beyond its own arguments.
    - `attempts >= limit` always forces `ESCALATE_TO_HUMAN` for an Alarm, regardless of what
      `policy` alone would decide: the Queen's own ceiling is a hard backstop, not a suggestion.

See Also:
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this table
      implements, and for "an issue a bee cannot resolve becomes an Alarm... Each level's
      EscalationPolicy is data".
    - hivemind.queen.autopilot.actions for QueenAction, this function's return type.
    - hivemind.supervision.policy for PolicyAction and decide, the Alarm-specific half this table
      delegates to.
    - hivemind.wardens.autopilot.table for decide, the sibling table this one's Alarm handling is
      modelled on.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.brood_chamber import TERMINAL_STATUSES, Task
from hivemind.queen.autopilot.actions import QueenAction
from hivemind.supervision import Alarm, EscalationPolicy, PolicyAction
from hivemind.supervision import decide as decide_policy
from hivemind.supervision.attendant import InboxItem
from waggle.messages.supervision import AlarmRaised, Answer, Heartbeat, Question
from waggle.messages.task import TaskOutcome, TaskResult

__all__ = ["decide"]

# Every PolicyAction the Queen's own EscalationPolicy can name, mapped onto what the Queen actually
# does about it. RESPAWN collapses onto RETRY_TASK for the same reason it does for a Warden: a
# retry at the Queen's level is always a fresh dispatch, never an in-place resume. TAKEOVER has no
# autopilot-level meaning here: the Queen's own takeover lever (Supervisor.intervene's Takeover) is
# a considered decision, so a policy row that names it is treated as "this needs a human's
# attention", the same conservative choice CANCEL's own ESCALATE fallback would make.
_POLICY_ACTION_MAP: Mapping[PolicyAction, QueenAction] = {
    PolicyAction.RETRY: QueenAction.RETRY_TASK,
    PolicyAction.RESPAWN: QueenAction.RETRY_TASK,
    PolicyAction.REBIND: QueenAction.REBIND,
    PolicyAction.TAKEOVER: QueenAction.ESCALATE_TO_HUMAN,
    PolicyAction.ESCALATE: QueenAction.ESCALATE_TO_HUMAN,
    PolicyAction.CANCEL: QueenAction.FAIL_TASK,
}


def decide(
    item: InboxItem, task: Task | None, attempts: int, policy: EscalationPolicy, limit: int
) -> QueenAction:
    """Return the one QueenAction the Queen's autopilot takes for `item`.

    Args:
        item: One already-scored, already-ordered inbox item; `item.payload` is the underlying
            waggle message.
        task: The task `item` concerns, when one exists (looked up by the caller); None when
            `item` names no task (a Heartbeat) or names one that could not be found.
        attempts: How many resolution attempts have already been made against this task or Alarm
            (the caller's own count, typically `task.attempt`); read only for a TaskResult(FAILED)
            or an AlarmRaised.
        policy: The Queen's own EscalationPolicy, consulted only for an AlarmRaised.
        limit: The most attempts an Alarm or a failing task gets before this table forces
            ESCALATE_TO_HUMAN or FAIL_TASK, whatever `policy` alone would say.

    Returns:
        Exactly one QueenAction; NEEDS_JUDGEMENT for a payload kind this table does not recognise.
    """
    payload = item.payload
    stale = task is not None and task.status in TERMINAL_STATUSES
    if isinstance(payload, TaskResult):
        return QueenAction.RECORD if stale else _decide_task_result(payload, attempts, limit)
    if isinstance(payload, AlarmRaised):
        return QueenAction.RECORD if stale else _decide_alarm(payload, attempts, policy, limit)
    if isinstance(payload, Question):
        return QueenAction.BLOCK_ON_QUESTION
    if isinstance(payload, Answer):
        return QueenAction.ROUTE_ANSWER
    if isinstance(payload, Heartbeat):
        return QueenAction.RECORD
    # A kind this table has never seen: hand off to queen.awake rather than silently dropping it.
    return QueenAction.NEEDS_JUDGEMENT


def _decide_task_result(payload: TaskResult, attempts: int, limit: int) -> QueenAction:
    """Map a TaskResult's own outcome to COMPLETE_TASK, RETRY_TASK or FAIL_TASK."""
    if payload.outcome is TaskOutcome.SUCCEEDED:
        return QueenAction.COMPLETE_TASK
    if payload.outcome is TaskOutcome.FAILED:
        # roadmap 3.22's own exit criteria: retries with attempt+1 up to the limit, then FAIL_TASK.
        return QueenAction.RETRY_TASK if attempts < limit else QueenAction.FAIL_TASK
    # CLAIMED never reaches the Queen (the Warden intercepts it); CANCELLED is the echo of a
    # cancellation the Queen herself already decided elsewhere. Either way, nothing more to decide.
    return QueenAction.RECORD


def _decide_alarm(
    payload: AlarmRaised, attempts: int, policy: EscalationPolicy, limit: int
) -> QueenAction:
    """Map an escalated AlarmRaised through the policy table, capped by the Queen's own ceiling."""
    # The Queen is the last supervisor before the human: attempts hitting the ceiling always wins,
    # whatever policy alone would decide for this kind (codingrules section 8.8, "the human is
    # last").
    if attempts >= limit:
        return QueenAction.ESCALATE_TO_HUMAN
    alarm = Alarm.from_wire(payload)
    keyed_alarm = alarm.model_copy(update={"attempts": attempts})
    return _POLICY_ACTION_MAP[decide_policy(policy, keyed_alarm)]
