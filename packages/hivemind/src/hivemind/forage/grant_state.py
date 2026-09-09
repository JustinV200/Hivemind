"""Define GrantState and the one transition table that governs every Forage grant's lifecycle.

A `ForageGrant` (`hivemind.forage.models.grants.ForageGrant`, the shared half of Forage -- the
Hive's capacity, modelled as data -- a Warden holds) moves through a fixed set of states from the
Queen's first issue to its eventual revocation. This module is the state machine codingrules
section 9 requires for every machine in the Hive: one `Enum` (`GrantState`) plus one transition
table (`TRANSITIONS`), each allowed edge commented with who causes it, tested edge by edge
(Appendix C, the "Forage grant" row). Nothing else in the Hive decides whether a transition is
legal; the Queen's dispatcher (a later phase 3 step) calls `assert_transition` before writing a
new `ForageGrant.state`.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Read by whatever writes a grant's
    state (the Queen's dispatcher, `hivemind.queen.dispatcher`, a later step) before every change.
    Calls into `hivemind.forage.errors` only.

Key invariants:
    - TRANSITIONS has exactly one entry per GrantState member, and REVOKED (the only terminal
      state) maps to an empty frozenset: a revoked grant never transitions again.
    - can_transition and assert_transition read TRANSITIONS only; neither hard-codes an edge.
    - Growing, shrinking or topping up a grant is a new `GrantIssued` revision at the wire layer
      (`waggle.messages.forage.grants.GrantIssued.revision`) and never itself a GrantState change;
      only becoming exhausted, recovering from exhaustion, or being revoked moves this state.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Forage grant" row, for the transition table this module
      implements.
    - hivemind.forage.errors for InvalidGrantTransitionError, the error assert_transition raises.
    - hivemind.forage.models.grants for ForageGrant, the model whose `state` field this machine
      governs.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.forage.errors import InvalidGrantTransitionError

__all__ = [
    "TERMINAL_STATES",
    "TRANSITIONS",
    "GrantState",
    "assert_transition",
    "can_transition",
    "is_terminal",
]


class GrantState(Enum):
    """Every state a Forage grant can be in, from first issue to revocation.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that.
    """

    ISSUED = "ISSUED"  # The Queen issued it; the holder has not yet drawn on it.
    ACTIVE = "ACTIVE"  # The holder is spending against it, within its terms.
    EXHAUSTED = "EXHAUSTED"  # A budget or seat reservation ran out; drawing further is refused.
    REVOKED = "REVOKED"  # Terminal: the Queen took it back, or the holder released it.


# The one terminal state: a grant TRANSITIONS maps to no further edges never changes state again
# (Appendix C: "terminal -> nothing"). Cross-checked by a test that walks TRANSITIONS rather than
# hard-coded a second time, so the two can never drift apart.
TERMINAL_STATES: frozenset[GrantState] = frozenset({GrantState.REVOKED})

# The single transition table (codingrules section 9): one entry per GrantState, each edge
# commented with who or what causes it. This is the only place that decides whether a transition
# is legal; every caller goes through can_transition/assert_transition rather than comparing states
# directly. Appendix C's "Forage grant" row names exactly ISSUED -> ACTIVE -> REVOKED,
# ACTIVE -> EXHAUSTED -> ACTIVE (top-up) and EXHAUSTED -> REVOKED (the holder was lost while the
# grant was spent: grants are leases that return to the pool when a Warden is dead or offline,
# codingrules 8.10, and a spent one has nothing worth topping up first); no other edge is named,
# so none is legal here.
TRANSITIONS: Mapping[GrantState, frozenset[GrantState]] = {
    GrantState.ISSUED: frozenset(
        {
            GrantState.ACTIVE,  # the holder starts drawing on the grant
        }
    ),
    GrantState.ACTIVE: frozenset(
        {
            GrantState.EXHAUSTED,  # a budget or seat reservation ran out
            GrantState.REVOKED,  # the Queen reclaimed it, or the holder released it
        }
    ),
    GrantState.EXHAUSTED: frozenset(
        {
            GrantState.ACTIVE,  # a top-up (a new GrantIssued revision) restored headroom
            GrantState.REVOKED,  # the holder went offline or died while spent; nothing to top up
        }
    ),
    GrantState.REVOKED: frozenset(),  # terminal: nothing follows
}


def can_transition(from_state: GrantState, to_state: GrantState) -> bool:
    """Return whether TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The grant's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: GrantState, to_state: GrantState, subject_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The grant's current state.
        to_state: The state a caller wants to move it to.
        subject_id: The grant's `GrantId`, when the caller has it, folded into the error message.

    Raises:
        InvalidGrantTransitionError: `to_state` is not one of the edges TRANSITIONS lists for
            `from_state`, for instance moving a REVOKED grant anywhere, or ISSUED straight to
            EXHAUSTED without an ACTIVE step in between.
    """
    # Every caller that changes a grant's state goes through this single check, so no edge is
    # ever legal anywhere the table itself does not list it.
    if not can_transition(from_state, to_state):
        raise InvalidGrantTransitionError(from_state, to_state, subject_id=subject_id)


def is_terminal(state: GrantState) -> bool:
    """Return whether `state` is one a grant never leaves.

    Args:
        state: The state to check.

    Returns:
        True if `state` is in TERMINAL_STATES.
    """
    return state in TERMINAL_STATES
