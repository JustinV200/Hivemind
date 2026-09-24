"""Define LoweringState and the one transition table a label lowering proposal moves through.

A lowering proposal asks to lower one Nectar's label (a raw deposit in the Honey Store, the Hive's
knowledge base, and the Honey rows ripened from it) below what the Real Cell floor holds it at
(ADR-0034). This module is the state machine codingrules section 9 requires for it: one `Enum`
(`LoweringState`) and one transition table (`TRANSITIONS`), each allowed edge commented with who
takes it. An edge here is also keyed by approver (`LabelApprover`: the independent judge or the
human), because ADR-0034 lets only the human lower a proposal the judge already rejected.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.lowering`.
    Read by the store's apply and reject transactions (`hivemind.honey_store.store.sqlite.
    lowering`), which call `assert_transition` before writing any edge. Calls into
    `hivemind.honey_store.clearance` (LabelApprover) and `hivemind.honey_store.errors` only.

Key invariants:
    - `TRANSITIONS` has one entry per `LoweringState`; `LOWERED` maps to no edge at all, so a
      lowered proposal never moves again (a later raise is an ordinary relabel).
    - `can_transition`/`assert_transition` read `TRANSITIONS` only; neither hard-codes an edge.
    - `REJECTED -> LOWERED` is the human's alone: the human is the last word (codingrules 8.8).

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the machine.
    - .claude/codingrules.md section 9 and Appendix C for the state-machine shape this follows.
    - hivemind.honey_store.errors for LoweringTransitionError, what assert_transition raises.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.errors import LoweringTransitionError

__all__ = [
    "TERMINAL_STATES",
    "TRANSITIONS",
    "LoweringState",
    "assert_transition",
    "can_transition",
]


class LoweringState(Enum):
    """Every state a label lowering proposal can be in; see `TRANSITIONS` for the legal moves."""

    PROPOSED = "PROPOSED"  # Filed; waiting for the judge, or for the human once noted.
    LOWERED = "LOWERED"  # Applied: the Nectar and its rows at the old label carry the target.
    REJECTED = "REJECTED"  # Kept at its label; only the human may still lower it.


# Either approver may take an edge marked with this set; codingrules 8.9's "a judge verdict or a
# human", the only two who may ever lower a label.
_JUDGE_OR_HUMAN = frozenset({LabelApprover.JUDGE, LabelApprover.HUMAN})

# The single transition table (codingrules section 9): for each state, the states it may move to
# and who may move it there. The only place that decides whether an edge is legal.
TRANSITIONS: Mapping[LoweringState, Mapping[LoweringState, frozenset[LabelApprover]]] = {
    LoweringState.PROPOSED: {
        # A judge's approval, or the human's.
        LoweringState.LOWERED: _JUDGE_OR_HUMAN,
        # A judge's rejection, the human's denial, or eligibility lost when it is applied.
        LoweringState.REJECTED: _JUDGE_OR_HUMAN,
    },
    LoweringState.REJECTED: {
        # The human only: the last word in the chain (codingrules 8.8); a judge never overturns
        # its own rejection, since it is asked once per Nectar.
        LoweringState.LOWERED: frozenset({LabelApprover.HUMAN}),
    },
    # Terminal: a lowered label that should rise again is an ordinary relabel, not a proposal.
    LoweringState.LOWERED: {},
}

# The states `TRANSITIONS` maps to no edge; cross-checked against the table by a test rather than
# derived from it, the same way hivemind.brood_chamber.task.state keeps its own.
TERMINAL_STATES: frozenset[LoweringState] = frozenset({LoweringState.LOWERED})


def can_transition(current: LoweringState, target: LoweringState, approver: LabelApprover) -> bool:
    """Return whether `TRANSITIONS` lets `approver` move a proposal from `current` to `target`.

    Args:
        current: The proposal's current state.
        target: The state it would move to.
        approver: Who asks for the move.

    Returns:
        True only when the table lists the edge and names `approver` on it.
    """
    return approver in TRANSITIONS[current].get(target, frozenset())


def assert_transition(
    current: LoweringState,
    target: LoweringState,
    approver: LabelApprover,
    proposal_id: str | None = None,
) -> None:
    """Raise unless `TRANSITIONS` lets `approver` move a proposal from `current` to `target`.

    Args:
        current: The proposal's current state.
        target: The state it would move to.
        approver: Who asks for the move.
        proposal_id: The proposal's id, when the caller has it, folded into the error message.

    Raises:
        LoweringTransitionError: The table has no such edge, or not for `approver`: a judge
            lowering a REJECTED proposal, anything leaving LOWERED, or REJECTED rejected again.
    """
    # Every write of a proposal's state passes this one check, inside the store's own
    # transaction, so no edge is ever legal anywhere the table does not list it.
    if not can_transition(current, target, approver):
        raise LoweringTransitionError(proposal_id, current.name, target.name, approver.name)
