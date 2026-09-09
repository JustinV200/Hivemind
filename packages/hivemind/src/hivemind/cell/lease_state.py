"""Define LeaseState and the one transition table that governs a Real Cell lease's lifecycle.

A `RealCellLease` (`hivemind.cell.lease`) moves through a fixed set of states from being asked for
to being released. This module is the state machine codingrules section 9 requires for every
machine in the Hive: one `Enum` (`LeaseState`) plus one transition table (`TRANSITIONS`), each
allowed edge commented with who causes it, tested edge by edge (codingrules Appendix C, the "Lease
(Real Cell)" row: "`REQUESTED -> OPEN -> RELEASING -> RELEASED`; `OPEN -> ORPHANED -> RELEASING`
(sweep)"). Nothing else in the Hive decides whether a lease transition is legal;
`hivemind.cell.lease.RealCellLease.open` and `.release` are the only callers of
`assert_transition`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by `hivemind.cell.lease` before
    every lease state change. Calls into `hivemind.cell.errors` only.

Key invariants:
    - TRANSITIONS has exactly one entry per LeaseState member, and RELEASED (the only terminal
      state) maps to an empty frozenset.
    - can_transition and assert_transition read TRANSITIONS only; neither hard-codes an edge.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Lease (Real Cell)" row, for the transition table this
      module implements.
    - hivemind.cell.errors for InvalidLeaseTransitionError, the error assert_transition raises.
    - hivemind.cell.lease for RealCellLease, the object whose `state` field this machine governs.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.cell.errors import InvalidLeaseTransitionError

__all__ = ["TRANSITIONS", "LeaseState", "assert_transition", "can_transition"]


class LeaseState(Enum):
    """Every state a Real Cell lease can be in, from request to release.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that.
    """

    REQUESTED = "REQUESTED"  # Asked for; the source has not opened it yet.
    OPEN = "OPEN"  # Opened: the lease's scratch directory exists and its session may be used.
    RELEASING = "RELEASING"  # release() is tearing down: killing processes, restoring paths.
    RELEASED = "RELEASED"  # Terminal: the device is left as found (or as restored as it gets).
    ORPHANED = "ORPHANED"  # Was OPEN but its holder is gone; found by a sweep, not by release().


# The single transition table (codingrules section 9): one entry per LeaseState, each edge
# commented with who or what causes it. This is the only place that decides whether a lease
# transition is legal; every caller goes through can_transition/assert_transition.
TRANSITIONS: Mapping[LeaseState, frozenset[LeaseState]] = {
    LeaseState.REQUESTED: frozenset({LeaseState.OPEN}),  # the source opens it (RealCellLease.open)
    LeaseState.OPEN: frozenset(
        {
            LeaseState.RELEASING,  # release() started
            LeaseState.ORPHANED,  # a sweep found the holder gone (no sweep lands this phase)
        }
    ),
    LeaseState.RELEASING: frozenset({LeaseState.RELEASED}),  # release()'s delegate finished
    LeaseState.RELEASED: frozenset(),  # terminal: nothing follows
    LeaseState.ORPHANED: frozenset({LeaseState.RELEASING}),  # the sweep releases what it found
}


def can_transition(from_state: LeaseState, to_state: LeaseState) -> bool:
    """Return whether TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The lease's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: LeaseState, to_state: LeaseState, lease_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The lease's current state.
        to_state: The state a caller wants to move it to.
        lease_id: The lease's id, when the caller has it, folded into the error message.

    Raises:
        InvalidLeaseTransitionError: `to_status` is not one of the edges TRANSITIONS lists for
            `from_state`, for instance releasing a lease that was never opened.
    """
    # Every caller that changes a RealCellLease's state goes through this single check
    # (hivemind.cell.lease's open() and release()), so no edge is ever legal anywhere the table
    # itself does not list it.
    if not can_transition(from_state, to_state):
        raise InvalidLeaseTransitionError(from_state, to_state, lease_id=lease_id)
