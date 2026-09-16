"""Define WaxState and the one transition table a Cell Wax note's whole life walks.

Appendix C's "Cell Wax note" row: ``PROPOSED -> WRITTEN -> CLEARED | EXPIRED``,
``PROPOSED -> REJECTED``. This module is the state machine codingrules section 9 requires for
every machine in the Hive, built exactly like ``hivemind.forage.grant_state`` (one ``Enum`` plus
one transition table, each edge commented, tested edge by edge): nothing outside
``hivemind.memory.cell_wax.writes`` decides whether a transition is legal, and that module calls
``assert_transition`` before every state change it makes.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the ``cell_wax`` sub-package.
    Read by ``hivemind.memory.cell_wax.writes`` before every state change; the field type of
    ``hivemind.memory.cell_wax.model.CellWax.state``. Calls into ``hivemind.memory.errors`` only.

Key invariants:
    - TRANSITIONS has exactly one entry per WaxState member; REJECTED, CLEARED and EXPIRED (the
      three terminal states) each map to an empty frozenset.
    - can_transition and assert_transition read TRANSITIONS only; neither hard-codes an edge.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Cell Wax note" row, for the transition table implemented
      here.
    - hivemind.forage.grant_state for GrantState/TRANSITIONS, the pattern this module mirrors.
    - hivemind.memory.errors for InvalidWaxTransitionError, the error assert_transition raises.
    - hivemind.memory.cell_wax.writes for propose_wax, write_wax, reject_wax, clear_wax and
      expire_wax, the only functions that call assert_transition.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.memory.errors import InvalidWaxTransitionError

__all__ = [
    "TERMINAL_STATES",
    "TRANSITIONS",
    "WaxState",
    "assert_transition",
    "can_transition",
    "is_terminal",
]


class WaxState(Enum):
    """Every state a Cell Wax note can be in, from proposal to its terminal outcome.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that.
    """

    PROPOSED = "PROPOSED"  # Anyone proposed it; not yet the Queen's own decision.
    WRITTEN = "WRITTEN"  # The Queen wrote it; it now weighs on placement while its Cell is in play.
    REJECTED = "REJECTED"  # Terminal: the Queen refused the proposal outright.
    CLEARED = "CLEARED"  # Terminal: the Queen cleared it, or it was retired with its Cell.
    EXPIRED = "EXPIRED"  # Terminal: its own expires_at passed on a House Bee sweep.


# The three terminal states: none of REJECTED, CLEARED or EXPIRED transitions further (Appendix C:
# "terminal -> nothing"). Cross-checked by a test that walks TRANSITIONS rather than hard-coding a
# second time, so the two can never drift apart.
TERMINAL_STATES: frozenset[WaxState] = frozenset(
    {WaxState.REJECTED, WaxState.CLEARED, WaxState.EXPIRED}
)

# The single transition table (codingrules section 9): one entry per WaxState, each edge commented
# with who or what causes it. Appendix C names exactly PROPOSED -> WRITTEN -> CLEARED | EXPIRED and
# PROPOSED -> REJECTED; no other edge is legal here. A Virtual Cell's wax retired at teardown
# (hivemind.memory.cell_wax.writes.retire_wax_for_cell) is still a WRITTEN -> CLEARED edge, the
# same one a Queen's own clear takes -- WaxClearCause on the row is what tells the two apart, not
# a separate state (a state machine names *where* a note is, not *why* it got there).
TRANSITIONS: Mapping[WaxState, frozenset[WaxState]] = {
    WaxState.PROPOSED: frozenset(
        {
            WaxState.WRITTEN,  # The Queen (autopilot or awake) wrote it.
            WaxState.REJECTED,  # The Queen refused it.
        }
    ),
    WaxState.WRITTEN: frozenset(
        {
            WaxState.CLEARED,  # The Queen cleared it, or its Cell was retired.
            WaxState.EXPIRED,  # Its own expires_at passed on a House Bee sweep.
        }
    ),
    WaxState.REJECTED: frozenset(),  # terminal: nothing follows
    WaxState.CLEARED: frozenset(),  # terminal: nothing follows
    WaxState.EXPIRED: frozenset(),  # terminal: nothing follows
}


def can_transition(from_state: WaxState, to_state: WaxState) -> bool:
    """Return whether TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The note's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: WaxState, to_state: WaxState, subject_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The note's current state.
        to_state: The state a caller wants to move it to.
        subject_id: The note's own id, when the caller has it, folded into the error message.

    Raises:
        InvalidWaxTransitionError: `to_state` is not one of the edges TRANSITIONS lists for
            `from_state`, for instance moving a REJECTED note anywhere, or PROPOSED straight to
            EXPIRED without a WRITTEN step in between.
    """
    # Every caller that changes a note's state goes through this single check, so no edge is ever
    # legal anywhere the table itself does not list it.
    if not can_transition(from_state, to_state):
        raise InvalidWaxTransitionError(from_state, to_state, subject_id=subject_id)


def is_terminal(state: WaxState) -> bool:
    """Return whether `state` is one a note never leaves.

    Args:
        state: The state to check.

    Returns:
        True if `state` is in TERMINAL_STATES.
    """
    return state in TERMINAL_STATES
