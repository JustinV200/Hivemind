"""Define ToolState and the one transition table a requested tool moves through.

A tool begins as a request from a bee, is scaffolded (its first files written), goes through the
Quarantine Comb (the sandbox every new tool must pass), and is either promoted into the Comb
Registry (the catalogue of promoted tools) or rejected; a promoted tool can later be retired.
This is the state machine codingrules section 9 and Appendix C ("Tool" row) require: one `Enum`
(`ToolState`) and one table (`TRANSITIONS`), each allowed edge commented. The scope (`hive` or
`cell`) is an attribute set at promotion, not a state.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside the royal_jelly package.
    Read by `hivemind.royal_jelly.registry` (retire checks `PROMOTED -> RETIRED` here) and by the
    promotion flow, which walks the earlier edges. Calls into `hivemind.royal_jelly.errors` only.

Key invariants:
    - `TRANSITIONS` has exactly one entry per `ToolState`; REJECTED and RETIRED have no edge out.
    - `can_transition` and `transition` read `TRANSITIONS` only; neither hard-codes an edge.

See Also:
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for the machine.
    - .claude/codingrules.md Appendix C for the table this implements.
    - hivemind.royal_jelly.errors for ToolTransitionError, what `transition` raises.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

from hivemind.royal_jelly.errors import ToolTransitionError

__all__ = ["TERMINAL_STATES", "TRANSITIONS", "ToolState", "can_transition", "transition"]


class ToolState(Enum):
    """Every state a tool can be in; see `TRANSITIONS` for the legal moves."""

    REQUESTED = "REQUESTED"  # A bee asked for it; nothing written yet.
    SCAFFOLDED = "SCAFFOLDED"  # The scaffolder produced a package (tool, tests, spec).
    QUARANTINING = "QUARANTINING"  # The Quarantine Comb is running its gates on the package.
    PROMOTED = "PROMOTED"  # In the Comb Registry; callable at its scope.
    REJECTED = "REJECTED"  # A gate failed or a refusal ended the attempt; never reopened.
    RETIRED = "RETIRED"  # Withdrawn from the registry; the row stays as history.


# The single transition table (codingrules section 9): for each state, the states it may move to.
TRANSITIONS: Mapping[ToolState, frozenset[ToolState]] = {
    # The scaffolder starts from the request; a request the scaffolder cannot turn into a valid
    # package at all (no reply, a spec that widens its requester) ends here, rejected.
    ToolState.REQUESTED: frozenset({ToolState.SCAFFOLDED, ToolState.REJECTED}),
    # A scaffolded package is sent to the Comb.
    ToolState.SCAFFOLDED: frozenset({ToolState.QUARANTINING}),
    # A passing report promotes; a failing report rejects; QUARANTINING -> SCAFFOLDED is the one
    # retry with the report: one retry with the report, roadmap 9.6.
    ToolState.QUARANTINING: frozenset(
        {ToolState.PROMOTED, ToolState.REJECTED, ToolState.SCAFFOLDED}
    ),
    # Only a promoted tool can be withdrawn.
    ToolState.PROMOTED: frozenset({ToolState.RETIRED}),
    # Terminal: a rejected request starts over as a new request.
    ToolState.REJECTED: frozenset(),
    # Terminal: a retired version is never resurrected (a rollback targets a PROMOTED row).
    ToolState.RETIRED: frozenset(),
}

# The states `TRANSITIONS` maps to no edge; cross-checked against the table by a test rather than
# derived from it, so a table edit that opens an exit from one fails loudly.
TERMINAL_STATES: frozenset[ToolState] = frozenset({ToolState.REJECTED, ToolState.RETIRED})


def can_transition(current: ToolState, target: ToolState) -> bool:
    """Return whether `TRANSITIONS` lists the edge `current -> target`.

    Args:
        current: The tool's current state.
        target: The state it would move to.

    Returns:
        True only when the table lists the edge.
    """
    return target in TRANSITIONS[current]


def transition(current: ToolState, target: ToolState) -> ToolState:
    """Return `target` once `TRANSITIONS` allows `current -> target`, else raise.

    Args:
        current: The tool's current state.
        target: The state it would move to.

    Returns:
        `target`, so a caller can write `state = transition(state, ToolState.RETIRED)`.

    Raises:
        ToolTransitionError: The table has no such edge.
    """
    # Every write of a tool's state passes this one check, so no edge is legal anywhere the table
    # does not list it.
    if not can_transition(current, target):
        raise ToolTransitionError(current.name, target.name)
    return target
