"""Define ProposalState and the one transition table that governs a Capping proposal's lifecycle.

A `Proposal` (`hivemind.supervision.capping.proposal`) moves through a fixed set of states from
being proposed to a terminal outcome. This module is the state machine codingrules section 9
requires for every machine in the Hive: one `Enum` (`ProposalState`) plus one transition table
(`TRANSITIONS`), each allowed edge commented with who causes it, tested edge by edge (codingrules
Appendix C, the "Proposal" row: "`PROPOSED -> CHECKING -> CAPPED -> APPLIED -> VERIFIED`;
`CHECKING -> REJECTED`; `APPLIED -> ROLLED_BACK`"). Nothing else in the Hive decides whether a
proposal transition is legal; `hivemind.supervision.capping.gate.CappingGate` is the only caller
of `assert_transition`. Mirrors `hivemind.cell.lease_state`'s shape exactly, because both are one
small state machine plus its table and nothing else.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Read by
    `hivemind.supervision.capping.gate` before every proposal state change. Calls into
    `hivemind.supervision.capping.errors` only.

Key invariants:
    - TRANSITIONS has exactly one entry per ProposalState member, and every terminal state
      (VERIFIED, REJECTED, ROLLED_BACK) maps to an empty frozenset.
    - can_transition, assert_transition and is_terminal read TRANSITIONS only; none hard-codes an
      edge or a terminal state.

See Also:
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Proposal" row, for the transition table this module
      implements.
    - hivemind.cell.lease_state for LeaseState and TRANSITIONS, the pattern this module mirrors.
    - hivemind.supervision.capping.errors for InvalidProposalTransitionError, the error
      assert_transition raises.
    - hivemind.supervision.capping.gate for CappingGate, this table's one caller.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.supervision.capping.errors import InvalidProposalTransitionError

__all__ = ["TRANSITIONS", "ProposalState", "assert_transition", "can_transition", "is_terminal"]


class ProposalState(Enum):
    """Every state a Capping proposal can be in, from being proposed to a terminal outcome.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that.
    """

    PROPOSED = "PROPOSED"  # Stored on the gate; propose() has recorded capping.proposed.
    CHECKING = "CHECKING"  # The tier's check ladder is running.
    CAPPED = "CAPPED"  # Every required check passed; ready to apply.
    APPLIED = "APPLIED"  # The action's side effect has run (a diff was written, a command ran).
    VERIFIED = "VERIFIED"  # Terminal: every postcondition held after applying.
    REJECTED = "REJECTED"  # Terminal: a check failed before anything was applied.
    ROLLED_BACK = "ROLLED_BACK"  # Terminal: applied, then undone because a postcondition failed.


# The single transition table (codingrules section 9): one entry per ProposalState, each edge
# commented with who or what causes it. This is the only place that decides whether a proposal
# transition is legal; every caller goes through can_transition/assert_transition.
TRANSITIONS: Mapping[ProposalState, frozenset[ProposalState]] = {
    ProposalState.PROPOSED: frozenset({ProposalState.CHECKING}),  # CappingGate.run starts checking
    ProposalState.CHECKING: frozenset(
        {
            ProposalState.CAPPED,  # every required check passed
            ProposalState.REJECTED,  # a check failed, or a required check was unavailable
        }
    ),
    ProposalState.CAPPED: frozenset({ProposalState.APPLIED}),  # the gate applies the action
    ProposalState.APPLIED: frozenset(
        {
            ProposalState.VERIFIED,  # every postcondition held
            ProposalState.ROLLED_BACK,  # a postcondition failed, or a COMMAND exited non-zero
        }
    ),
    ProposalState.VERIFIED: frozenset(),  # terminal: nothing follows
    ProposalState.REJECTED: frozenset(),  # terminal: nothing follows
    ProposalState.ROLLED_BACK: frozenset(),  # terminal: nothing follows
}


def can_transition(from_state: ProposalState, to_state: ProposalState) -> bool:
    """Return whether TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The proposal's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: ProposalState, to_state: ProposalState, proposal_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The proposal's current state.
        to_state: The state a caller wants to move it to.
        proposal_id: The proposal's id, when the caller has it, folded into the error message.

    Raises:
        InvalidProposalTransitionError: `to_state` is not one of the edges TRANSITIONS lists for
            `from_state`, for instance applying a proposal that was never capped.
    """
    # Every caller that advances a proposal's state goes through this single check
    # (hivemind.supervision.capping.gate.CappingGate), so no edge is ever legal anywhere the
    # table itself does not list it.
    if not can_transition(from_state, to_state):
        raise InvalidProposalTransitionError(from_state, to_state, proposal_id=proposal_id)


def is_terminal(state: ProposalState) -> bool:
    """Return whether `state` has no outgoing edges in TRANSITIONS.

    Used by `CappingGate.pending()` to filter out proposals that have already reached a final
    outcome (VERIFIED, REJECTED or ROLLED_BACK).

    Args:
        state: The state to check.

    Returns:
        True if TRANSITIONS maps `state` to the empty set.
    """
    return not TRANSITIONS[state]
