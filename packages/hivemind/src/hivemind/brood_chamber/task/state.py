"""Define TaskStatus and the one transition table that governs every task's lifecycle.

A Task (`hivemind.brood_chamber.task.model.Task`) moves through a fixed set of states from
submission to a terminal outcome. This module is the state machine codingrules section 9 requires
for every machine in the Hive: one `Enum` (`TaskStatus`) plus one transition table
(`TRANSITIONS`), each allowed edge commented with who causes it, tested edge by edge (Appendix C,
the "Task" row). Nothing else in the Hive decides whether a transition is legal; `hivemind.
brood_chamber.chamber` (roadmap step 2.8) calls `assert_transition` before writing a new `Task.
status`, and `hivemind.brood_chamber.task.model.Task`'s own validators check the *consequences* of
a status (whether `outcome`, `warden_id` and so on are set) without re-deciding which transitions
exist.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by `hivemind.brood_chamber.chamber`
    (roadmap step 2.8) before every state change, and by `hivemind.brood_chamber.task.graph` (which
    compares a task's status to `TaskStatus.PENDING` / `TaskStatus.SUCCEEDED`, not the table
    itself). Calls into `hivemind.brood_chamber.errors` only.

Key invariants:
    - TRANSITIONS has exactly one entry per TaskStatus member, and every terminal status maps to
      an empty frozenset: a terminal task never transitions again.
    - can_transition and assert_transition read TRANSITIONS only; neither hard-codes an edge.
    - TERMINAL_STATUSES is exactly the set of TaskStatus members TRANSITIONS maps to no edges.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Task" row, for the transition table this module
      implements and the note that SUCCEEDED requires the Warden's acceptance checks to pass.
    - hivemind.brood_chamber.errors for InvalidTransitionError, the error assert_transition raises.
    - hivemind.brood_chamber.task.model for Task, the model whose `status` field this machine
      governs.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.brood_chamber.errors import InvalidTransitionError

__all__ = [
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "TaskStatus",
    "assert_transition",
    "can_transition",
    "is_terminal",
]


class TaskStatus(Enum):
    """Every state a Task can be in, from submission to a terminal outcome.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that.
    """

    PENDING = "PENDING"  # Submitted, not yet placed on a Cell.
    ASSIGNED = "ASSIGNED"  # Placement chose a Cell and a Warden; the Worker has not started.
    RUNNING = "RUNNING"  # A Worker is actively working the task on its assigned Cell.
    BLOCKED = "BLOCKED"  # Waiting on an Answer to a Question it asked.
    PAUSED = "PAUSED"  # Suspended by Clustering (a provider outage), preserved by a Handoff.
    SUCCEEDED = "SUCCEEDED"  # Terminal: the Warden ran acceptance checks and they passed.
    FAILED = "FAILED"  # Terminal: acceptance checks did not pass, or the Worker gave up.
    CANCELLED = "CANCELLED"  # Terminal: a human or the Queen cancelled the goal.


# The three statuses TRANSITIONS maps to no further edges; a task in one of these never changes
# status again (Appendix C: "terminal -> nothing"). Defined from TRANSITIONS below it would create
# an import-time ordering dependency for no benefit, so it is instead cross-checked by a test that
# walks TRANSITIONS and asserts this set is exactly the statuses with an empty edge set.
TERMINAL_STATUSES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

# The single transition table (codingrules section 9): one entry per TaskStatus, each edge
# commented with who or what causes it. This is the only place that decides whether a transition
# is legal; every caller goes through can_transition/assert_transition rather than comparing
# statuses directly.
TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.ASSIGNED,  # placement chose a Cell and a Warden
            TaskStatus.CANCELLED,  # a human or the Queen cancels the goal
        }
    ),
    TaskStatus.ASSIGNED: frozenset(
        {
            TaskStatus.RUNNING,  # the Warden's Worker started
            TaskStatus.PENDING,  # the Warden was lost before the Worker started
            TaskStatus.CANCELLED,  # a human or the Queen cancels the goal
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,  # acceptance checks passed, run by the Warden
            TaskStatus.FAILED,  # acceptance checks failed, or the Worker gave up
            TaskStatus.CANCELLED,  # a human or the Queen cancels the goal
            TaskStatus.BLOCKED,  # the Worker or Warden asked a Question
            TaskStatus.PAUSED,  # Clustering: the task's provider set became unavailable
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.RUNNING,  # the Question was answered or withdrawn
            TaskStatus.CANCELLED,  # a human or the Queen cancels the goal
        }
    ),
    TaskStatus.PAUSED: frozenset(
        {
            TaskStatus.RUNNING,  # Clustering resumed
            TaskStatus.CANCELLED,  # a human or the Queen cancels the goal
        }
    ),
    TaskStatus.SUCCEEDED: frozenset(),  # terminal: nothing follows
    TaskStatus.FAILED: frozenset(),  # terminal: nothing follows
    TaskStatus.CANCELLED: frozenset(),  # terminal: nothing follows
}


def can_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """Return whether TRANSITIONS allows moving from `from_status` to `to_status`.

    Args:
        from_status: The task's current status.
        to_status: The status a caller wants to move it to.

    Returns:
        True if `to_status` is one of the edges TRANSITIONS lists for `from_status`.
    """
    return to_status in TRANSITIONS[from_status]


def assert_transition(
    from_status: TaskStatus, to_status: TaskStatus, task_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_status` to `to_status`.

    Args:
        from_status: The task's current status.
        to_status: The status a caller wants to move it to.
        task_id: The task's id, when the caller has it, folded into the error message.

    Raises:
        InvalidTransitionError: `to_status` is not one of the edges TRANSITIONS lists for
            `from_status`, for instance moving a SUCCEEDED task anywhere, or PENDING straight to
            RUNNING without an ASSIGNED step in between.
    """
    # Every caller that changes a Task's status goes through this single check (roadmap step
    # 2.8's chamber.py), so no edge is ever legal anywhere the table itself does not list it.
    if not can_transition(from_status, to_status):
        raise InvalidTransitionError(from_status, to_status, subject_id=task_id)


def is_terminal(status: TaskStatus) -> bool:
    """Return whether `status` is one a task never leaves.

    Args:
        status: The status to check.

    Returns:
        True if `status` is in TERMINAL_STATUSES.
    """
    return status in TERMINAL_STATUSES
