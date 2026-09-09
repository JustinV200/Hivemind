"""Define WorkerState and the one transition table a Worker's life moves through.

A Worker (a subagent a Warden spawns to run one role, such as a Drone) moves through a fixed set
of states from being spawned to a terminal outcome. This module is the state machine codingrules
section 9 requires for every machine in the Hive: one `Enum` (`WorkerState`) plus one transition
table (`TRANSITIONS`), each allowed edge commented with who causes it, tested edge by edge
(codingrules Appendix C, the "Worker" row: "`SPAWNED -> RUNNING -> DONE / FAILED / KILLED`;
`RUNNING <-> HANDING_OFF` (reset, rebind, migrate); `RUNNING <-> PAUSED`"). `WorkerState` also
mirrors `waggle.messages.supervision.WorkerState` member for member (codingrules section 6.1: "a
hivemind mirror of waggle's label enums" carries `from_wire`/`to_wire`), because the same states
travel on `Heartbeat` and `ChildTelemetry`. Nothing else in the Hive decides whether a Worker
transition is legal; `hivemind.workers.runtime.WorkerRuntime` is the one caller of
`assert_transition`.

Fits into the Hive:
    Layer 4 (roles that do the work). Read by `hivemind.workers.runtime` (roadmap step 3.15)
    before every state change it makes, and by `hivemind.wardens` (roadmap step 3.19), which
    tracks the same states from a sub-bee's Heartbeat rows. Calls into `hivemind.workers.errors`
    and waggle only.

Key invariants:
    - WorkerState's member names and values are identical to
      `waggle.messages.supervision.WorkerState`'s (`tests/unit/workers/test_state.py` checks it
      member for member).
    - TRANSITIONS has exactly one entry per WorkerState member; DONE, FAILED and KILLED each map
      to an empty frozenset, the three states a Worker never leaves once it reaches one.
    - can_transition, assert_transition and is_terminal read TRANSITIONS only; none hard-codes an
      edge.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Worker" row, for the transition table this module
      implements.
    - hivemind.workers.errors for InvalidWorkerTransitionError, the error assert_transition
      raises.
    - hivemind.workers.runtime for WorkerRuntime, the object whose life this machine governs.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.workers.errors import InvalidWorkerTransitionError
from waggle.messages.supervision import WorkerState as WireWorkerState

__all__ = ["TRANSITIONS", "WorkerState", "assert_transition", "can_transition", "is_terminal"]


class WorkerState(Enum):
    """Every state a Worker can be in, from being spawned to a terminal outcome.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that. Mirrors
    `waggle.messages.supervision.WorkerState` member for member.
    """

    SPAWNED = "SPAWNED"  # Constructed by its Warden; has not yet received a TaskAssign.
    RUNNING = "RUNNING"  # A role's coroutine is running (fresh, resumed, or unpaused).
    HANDING_OFF = "HANDING_OFF"  # Writing a Handoff before a reset, rebind, migrate or stop.
    PAUSED = "PAUSED"  # Held in place by TaskPause; the role awaits its own pause event.
    DONE = "DONE"  # Terminal: the role claimed the work done, or stopped after a handoff.
    FAILED = "FAILED"  # Terminal: the role's own coroutine raised.
    KILLED = "KILLED"  # Terminal: TaskCancel, or an Intervene(CANCEL), ended the attempt.

    @classmethod
    def from_wire(cls, wire: WireWorkerState) -> WorkerState:
        """Convert the wire form of this state into hivemind's own enum.

        Args:
            wire: The `waggle.messages.supervision.WorkerState` value read off an Envelope.

        Returns:
            The hivemind WorkerState member with the same name.
        """
        # Values are identical strings on both sides (the sync test enforces it), so a plain
        # value lookup is the whole conversion.
        return cls(wire.value)

    def to_wire(self) -> WireWorkerState:
        """Convert this state into the wire form waggle.messages carries on an Envelope.

        Returns:
            The `waggle.messages.supervision.WorkerState` member with the same name.
        """
        return WireWorkerState(self.value)


# The single transition table (codingrules section 9): one entry per WorkerState, each edge
# commented with who or what causes it. This is the only place that decides whether a Worker
# transition is legal; every caller goes through can_transition/assert_transition.
TRANSITIONS: Mapping[WorkerState, frozenset[WorkerState]] = {
    WorkerState.SPAWNED: frozenset(
        {
            WorkerState.RUNNING,  # TaskAssign arrives; the role's coroutine starts.
        }
    ),
    WorkerState.RUNNING: frozenset(
        {
            WorkerState.HANDING_OFF,  # A checkpoint threshold, Compact or Checkpoint intervene.
            WorkerState.PAUSED,  # TaskPause.
            WorkerState.DONE,  # The role returned WorkerOutcome(claimed=True).
            WorkerState.FAILED,  # The role's coroutine raised.
            WorkerState.KILLED,  # TaskCancel, or Intervene(CANCEL), and the grace period ran out.
        }
    ),
    WorkerState.HANDING_OFF: frozenset(
        {
            WorkerState.RUNNING,  # The checkpoint was written and the role restarts from it.
            WorkerState.DONE,  # HANDOFF/REBIND/TAKEOVER told the runtime to stop after handing off.
            WorkerState.KILLED,  # TaskCancel arrived while the checkpoint was being written.
        }
    ),
    WorkerState.PAUSED: frozenset(
        {
            WorkerState.RUNNING,  # TaskResume.
            WorkerState.KILLED,  # TaskCancel arrived while paused.
        }
    ),
    WorkerState.DONE: frozenset(),  # terminal: nothing follows
    WorkerState.FAILED: frozenset(),  # terminal: nothing follows
    WorkerState.KILLED: frozenset(),  # terminal: nothing follows
}


def can_transition(from_state: WorkerState, to_state: WorkerState) -> bool:
    """Return whether TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The Worker's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: WorkerState, to_state: WorkerState, worker_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The Worker's current state.
        to_state: The state a caller wants to move it to.
        worker_id: The Worker's id, when the caller has it, folded into the error message.

    Raises:
        InvalidWorkerTransitionError: `to_state` is not one of the edges TRANSITIONS lists for
            `from_state`, for instance pausing a Worker that has already reached DONE.
    """
    # Every caller that advances a Worker's state goes through this single check
    # (hivemind.workers.runtime.WorkerRuntime), so no edge is ever legal anywhere the table
    # itself does not list it.
    if not can_transition(from_state, to_state):
        raise InvalidWorkerTransitionError(from_state, to_state, worker_id=worker_id)


def is_terminal(state: WorkerState) -> bool:
    """Return whether `state` is one a Worker never leaves once it reaches it.

    Args:
        state: The state to check.

    Returns:
        True for DONE, FAILED and KILLED (TRANSITIONS maps each to an empty frozenset); False
        otherwise.
    """
    return not TRANSITIONS[state]
