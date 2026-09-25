"""Define WardenState and the one transition table a Warden's life moves through.

A Warden (the per-Cell supervisor that spawns and supervises Workers on exactly one Cell, a unit of
compute) moves through a fixed set of states from starting up to being stopped. This module is the
state machine codingrules section 9 requires for every machine in the Hive: one `Enum`
(`WardenState`) plus one transition table (`TRANSITIONS`), each allowed edge commented with who
causes it, tested edge by edge (codingrules Appendix C, the "Warden" row: "`STARTING -> ACTIVE`;
`ACTIVE <-> WATCH` (Real Cells, no active bees); `ACTIVE / WATCH -> OFFLINE -> ACTIVE`; any ->
`CLUSTERED -> ACTIVE`; `ACTIVE -> MIGRATING -> ACTIVE` (new host); `-> STOPPED`"). `WardenState`
also mirrors `waggle.messages.supervision.WardenState` member for member (codingrules section
6.1: "a hivemind mirror of waggle's label enums" carries `from_wire`/`to_wire`), because the same
states travel on `Heartbeat`. Nothing else in the Hive decides whether a Warden transition is legal;
`hivemind.wardens.warden.Warden` is the one caller of `assert_transition`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Read by
    `hivemind.wardens.warden.Warden` before every state change it makes, and by the Queen (a later
    roadmap step), which tracks the same states from a Warden's Heartbeat. Calls into
    `hivemind.wardens.errors` and waggle only.

Key invariants:
    - WardenState's member names and values are identical to
      `waggle.messages.supervision.WardenState`'s (`tests/unit/wardens/test_state.py` checks it
      member for member).
    - TRANSITIONS has exactly one entry per WardenState member; STOPPED maps to an empty
      frozenset, the one state a Warden never leaves once it reaches it.
    - can_transition, assert_transition and is_terminal read TRANSITIONS only; none hard-codes an
      edge.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Warden" row, for the transition table this module
      implements.
    - hivemind.wardens.errors for InvalidWardenTransitionError, the error assert_transition raises.
    - hivemind.wardens.warden for Warden, the object whose life this machine governs.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from enum import Enum

from hivemind.wardens.errors import InvalidWardenTransitionError
from waggle.ids import TaskId
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.supervision import WardenState as WireWardenState
from waggle.messages.task import TaskResume

__all__ = [
    "SETTLED_EVENT_KINDS",
    "TRANSITIONS",
    "WardenState",
    "assert_transition",
    "can_transition",
    "clustering_update",
    "is_terminal",
    "settled_state",
]


class WardenState(Enum):
    """Every state a Warden can be in, from starting up to being stopped.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that. Mirrors
    `waggle.messages.supervision.WardenState` member for member.
    """

    STARTING = "STARTING"  # Constructed; leasing its Cell and opening its own session.
    ACTIVE = "ACTIVE"  # Leased, with at least one sub-bee running (or about to be).
    WATCH = "WATCH"  # A Real Cell's Warden with no active sub-bees, observing read-only.
    OFFLINE = "OFFLINE"  # Cut off from the Queen; running on its own outbox and local Forage.
    CLUSTERED = "CLUSTERED"  # Paused and preserved while a model provider is down.
    MIGRATING = "MIGRATING"  # Moving to another host (a Supersedure or promotion step).
    STOPPED = "STOPPED"  # Terminal: shut down; its lease released.

    @classmethod
    def from_wire(cls, wire: WireWardenState) -> WardenState:
        """Convert the wire form of this state into hivemind's own enum.

        Args:
            wire: The `waggle.messages.supervision.WardenState` value read off an Envelope.

        Returns:
            The hivemind WardenState member with the same name.
        """
        # Values are identical strings on both sides (the sync test enforces it), so a plain
        # value lookup is the whole conversion.
        return cls(wire.value)

    def to_wire(self) -> WireWardenState:
        """Convert this state into the wire form waggle.messages carries on an Envelope.

        Returns:
            The `waggle.messages.supervision.WardenState` member with the same name.
        """
        return WireWardenState(self.value)


# The single transition table (codingrules section 9): one entry per WardenState, each edge
# commented with who or what causes it. This is the only place that decides whether a Warden
# transition is legal; every caller goes through can_transition/assert_transition.
#
# Appendix C's own notation is compressed ("ACTIVE / WATCH -> OFFLINE -> ACTIVE", "any ->
# CLUSTERED -> ACTIVE", a bare "-> STOPPED" with no state named). Read literally and expanded to
# one edge per line below: every non-terminal state can reach CLUSTERED (a provider outage can
# strike a Warden in any of them) and every non-terminal state can reach STOPPED (a Warden must be
# stoppable from wherever it happens to be, the same way `waggle.loop.TickLoop.stop()` is always
# legal to call); OFFLINE and WATCH both resolve back to ACTIVE, never to STARTING, because
# reconnecting or a sub-bee arriving never re-runs the lease step.
TRANSITIONS: Mapping[WardenState, frozenset[WardenState]] = {
    WardenState.STARTING: frozenset(
        {
            WardenState.ACTIVE,  # The lease opened and at least one sub-bee is expected/running.
            WardenState.WATCH,  # LeaseRefusedError: the Hive Stand's Warden exists with no lease.
        }
    ),
    WardenState.ACTIVE: frozenset(
        {
            WardenState.WATCH,  # The last sub-bee finished; nothing left to supervise.
            WardenState.OFFLINE,  # The link to the Queen was lost.
            WardenState.CLUSTERED,  # A bound provider went down with no fallback.
            WardenState.MIGRATING,  # A Supersedure or promotion moves this Warden's own host.
            WardenState.STOPPED,  # Warden.stop() was called.
        }
    ),
    WardenState.WATCH: frozenset(
        {
            WardenState.ACTIVE,  # A spawn (TaskAssign + GrantIssued both arrived).
            WardenState.OFFLINE,  # The link to the Queen was lost while idle.
            WardenState.CLUSTERED,  # A bound provider went down while idle (Patrol's own model).
            WardenState.STOPPED,  # Warden.stop() was called.
        }
    ),
    WardenState.OFFLINE: frozenset(
        {
            WardenState.ACTIVE,  # The link to the Queen came back, with sub-bees still running.
            WardenState.CLUSTERED,  # The outage crossed the manifest's max offline duration.
            WardenState.STOPPED,  # Warden.stop() was called while offline.
        }
    ),
    WardenState.CLUSTERED: frozenset(
        {
            WardenState.ACTIVE,  # The provider came back; paused sub-bees resume from Handoff.
            WardenState.STOPPED,  # Warden.stop() was called while clustered.
        }
    ),
    WardenState.MIGRATING: frozenset(
        {
            WardenState.ACTIVE,  # The move to the new host completed.
            WardenState.STOPPED,  # The move failed outright and this Warden is torn down instead.
        }
    ),
    WardenState.STOPPED: frozenset(),  # terminal: nothing follows
}


def can_transition(from_state: WardenState, to_state: WardenState) -> bool:
    """Return whether TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The Warden's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: WardenState, to_state: WardenState, warden_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The Warden's current state.
        to_state: The state a caller wants to move it to.
        warden_id: The Warden's id, when the caller has it, folded into the error message.

    Raises:
        InvalidWardenTransitionError: `to_state` is not one of the edges TRANSITIONS lists for
            `from_state`, for instance moving a STOPPED Warden anywhere.
    """
    # Every caller that advances a Warden's state goes through this single check
    # (hivemind.wardens.warden.Warden), so no edge is ever legal anywhere the table itself does
    # not list it.
    if not can_transition(from_state, to_state):
        raise InvalidWardenTransitionError(from_state, to_state, warden_id=warden_id)


def is_terminal(state: WardenState) -> bool:
    """Return whether `state` is one a Warden never leaves once it reaches it.

    Args:
        state: The state to check.

    Returns:
        True for STOPPED (TRANSITIONS maps it to an empty frozenset); False otherwise.
    """
    return not TRANSITIONS[state]


# The trail kind a Warden records when it settles into each of the three states
# `settled_state` can return (roadmap step 4.9 added CLUSTERED); Appendix C's rule 3: every
# transition is a trail event written in the same tick that made it.
SETTLED_EVENT_KINDS: dict[WardenState, str] = {
    WardenState.ACTIVE: "warden.active",
    WardenState.WATCH: "warden.watch",
    WardenState.CLUSTERED: "warden.clustered",
}


def settled_state(task_ids: Iterable[TaskId | None], clustered: Collection[TaskId]) -> WardenState:
    """Decide which of ACTIVE/WATCH/CLUSTERED a Warden belongs in, from its own sub-bees.

    Roadmap step 4.9 (Clustering): the pure half of `hivemind.wardens.warden.Warden`'s own
    ACTIVE <-> CLUSTERED decision, split out here (rather than inline in `warden.py`) purely to
    keep that module within codingrules 5.1's own file-size limit -- a Warden's sub-bee table and
    its own Queen-tracked clustered-task set are both plain data this machine's own file may read
    without adding a `hivemind.wardens.spawn`/`.warden` import.

    A bee the Queen paused this way writes its Handoff and stops, and its row is retired the
    moment it reports so (`hivemind.wardens.ticks.heartbeat.record_heartbeat`), so a Warden whose
    every task is paused can hold no sub-bee at all: it stays CLUSTERED on the paused tasks alone
    until the Queen resumes one (her fresh TaskAssign drops it from `clustered`,
    `hivemind.wardens.ticks.assign`). Reading "no sub-bees" as WATCH there asked for CLUSTERED ->
    WATCH, which Appendix C has no edge for, and ended the Warden's own tick loop.

    Args:
        task_ids: One entry per current sub-bee, its own task id (None never matches `clustered`).
        clustered: Every task id a Queen-sent Intervene(HANDOFF) currently has paused.

    Returns:
        CLUSTERED when `clustered` is non-empty and every sub-bee's task (if any) is in it; ACTIVE
        when some sub-bee's task is not; WATCH when there is neither a sub-bee nor a paused task.
    """
    ids = tuple(task_ids)
    if clustered and all(task_id in clustered for task_id in ids):
        return WardenState.CLUSTERED
    return WardenState.ACTIVE if ids else WardenState.WATCH


def clustering_update(
    task_id: TaskId | None, payload: object, clustered_tasks: set[TaskId]
) -> None:
    """Mutate `clustered_tasks` in place from one already-Queen-sourced control message.

    Roadmap step 4.9: the pure-in-effect-shape half of `hivemind.wardens.warden.Warden`'s own
    bookkeeping for `warden._clustered_tasks`, split out for the same file-size reason
    `settled_state`'s own docstring gives. The one call site (`hivemind.wardens.ticks.control.
    forward_control`, the `FORWARD_CONTROL` handler) checks `item.principal == _QUEEN_LINK` itself
    before calling this at all: a sub-bee's own link never sends an `Intervene(HANDOFF)`/
    `TaskResume` to its own Warden, so nothing here re-checks the sender.

    Args:
        task_id: The InboxItem's own task id (`item.task_id`); a no-op when None.
        payload: The control message this item carried.
        clustered_tasks: `warden._clustered_tasks`, mutated in place.
    """
    if task_id is None:
        return
    if isinstance(payload, Intervene) and payload.action is InterventionAction.HANDOFF:
        clustered_tasks.add(task_id)
    elif isinstance(payload, TaskResume):
        clustered_tasks.discard(task_id)
