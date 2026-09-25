"""Define the taint label's one transition table: unlabelled or CLEARED -> TAINTED -> CLEARED.

Appendix C's "Taint label" row (roadmap step 10.6d, ADR-0043). The label is a small state machine
codingrules section 9 requires a single table for, built like `hivemind.memory.cell_wax.state`: an
item with no marker (`None`) may become TAINTED (the one setter, `hivemind.memory.taint.set.
taint_memory`); a TAINTED item may become CLEARED (the one clearer, `hivemind.memory.taint.clear.
clear_taint`, on a judge verdict); and a CLEARED item may become TAINTED again, because a later
incident (a second isolation covering the same memory) is a new reason to refuse it. Nothing moves
an item back to unlabelled: a cleared marker keeps its history on the item. Tainting a TAINTED
item and clearing one that is not TAINTED are forbidden, so neither setter nor clearer can quietly
overwrite the event that labelled an item. Every memory store checks this table inside the same
transaction that writes the label, so a concurrent writer can never slip an illegal edge past it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`. Called by
    every `TaintLedger.write_taint` implementation (the memory stores today, the Honey Store from
    phase 7) before it writes a label. Calls into `hivemind.memory.errors` and this package's
    `marker` only.

Key invariants:
    - TRANSITIONS has exactly one entry per starting point (None, TAINTED, CLEARED); no edge ever
      leads back to None.
    - can_transition and assert_transition read TRANSITIONS only; neither hard-codes an edge.

See Also:
    - .claude/codingrules.md section 9 and Appendix C, "Taint label" row.
    - hivemind.memory.cell_wax.state for the pattern this module mirrors.
    - hivemind.memory.errors for InvalidTaintTransitionError.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from hivemind.memory.errors import InvalidTaintTransitionError
from hivemind.memory.taint.marker import TaintState

__all__ = ["TRANSITIONS", "assert_transition", "can_transition"]

# The single transition table (codingrules section 9), keyed by where a label starts (None for an
# item never labelled), each edge commented with the one function that takes it.
TRANSITIONS: Mapping[TaintState | None, frozenset[TaintState]] = MappingProxyType(
    {
        None: frozenset({TaintState.TAINTED}),  # taint_memory: isolation, quarantine, a report.
        TaintState.TAINTED: frozenset({TaintState.CLEARED}),  # clear_taint, on a judge verdict.
        TaintState.CLEARED: frozenset({TaintState.TAINTED}),  # taint_memory: a later incident.
    }
)


def can_transition(from_state: TaintState | None, to_state: TaintState) -> bool:
    """Return whether TRANSITIONS allows moving a label from `from_state` to `to_state`.

    Args:
        from_state: The label's current state; None for an item never labelled.
        to_state: The state a caller wants to write.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: TaintState | None, to_state: TaintState, item_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving a label from `from_state` to `to_state`.

    Args:
        from_state: The label's current state; None for an item never labelled.
        to_state: The state a caller wants to write.
        item_id: The item's own id, folded into the error message when given.

    Raises:
        InvalidTaintTransitionError: The edge is not in TRANSITIONS: tainting a TAINTED item, or
            clearing one that is unlabelled or already CLEARED.
    """
    # Every label write goes through this one check, inside the store's own transaction.
    if not can_transition(from_state, to_state):
        raise InvalidTaintTransitionError(from_state, to_state, item_id=item_id)
