"""Define Proposal: what a bee wants to do, its risk tier, and the postconditions it expects.

Codingrules section 8.12: "Propose, then commit." A `Proposal` is a Worker's (a role-playing
sub-bee's) request to the Capping gate before any side effect outside a lease's scratch directory
lands: the action itself (`waggle.messages.capping.ProposedAction`, carried directly -- it is a
value model with no behaviour, codingrules section 6.1), the `RiskTier` it declares, the
`Postcondition`s (`waggle.messages.labels.Postcondition`, also carried directly) it expects to
hold afterwards, and why. `state` tracks where it is in `hivemind.supervision.capping.state
.ProposalState`'s lifecycle; every other field is fixed once proposed.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Built
    by a Worker's tools (`hivemind.workers.tools`, roadmap step 3.16) and handed to
    `hivemind.supervision.capping.gate.CappingGate.propose`; read by every deterministic check
    (`hivemind.supervision.capping.checks`) and by `hivemind.supervision.capping.apply`. Calls
    into `hivemind.cell` (HoneyClearance), `hivemind.forage.tempo` (Tempo),
    `hivemind.supervision.capping.state` and `hivemind.supervision.capping.tiers` and waggle only.

Key invariants:
    - Proposal is frozen and forbids extras, like every boundary value in this repository; a state
      change produces a new Proposal via `model_copy(update={"state": ...})`
      (`hivemind.supervision.capping.gate.CappingGate` is the only caller that does this).
    - `state` defaults to `ProposalState.PROPOSED`, matching a freshly built proposal that has not
      yet been handed to a gate.

See Also:
    - .claude/codingrules.md section 8.12 for "Propose, then commit" and the postconditions rule.
    - .claude/codingrules.md Appendix C, "Proposal" row, for the state machine `state` moves along.
    - waggle.messages.capping for ProposedAction and RiskTier's wire form.
    - waggle.messages.labels for Postcondition, the value model carried directly.
    - hivemind.forage.tempo for Tempo, the speed-against-accuracy setting a proposal carries from
      its task.
    - hivemind.supervision.capping.tiers for the hivemind-side RiskTier mirror this module uses.
    - hivemind.supervision.capping.gate for CappingGate, which stores and advances a Proposal.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.forage.tempo import Tempo
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.base import (
    MAX_REASON_CHARS,
    CellIdField,
    MessageIdField,
    TaskIdField,
    WorkerIdField,
)
from waggle.messages.capping import ProposedAction
from waggle.messages.labels import Postcondition

MAX_POSTCONDITIONS = 32  # Mirrors waggle.messages.capping.proposals.MAX_POSTCONDITIONS.
MIN_SPEND_ESTIMATE_USD = 0.0  # Spend is never negative.

# A reason field, matching the shared convention (waggle.messages.base.MAX_REASON_CHARS): always
# bounded, so a proposal's own "why" fits the same budget its waggle wire form does.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]

__all__ = ["MAX_POSTCONDITIONS", "Proposal"]


class Proposal(BaseModel):
    """What a bee wants to do, checked, applied and verified by the Capping gate.

    Built once, by whichever Worker tool wants to make a side-effecting change, and handed to
    `CappingGate.propose`; `state` is the only field that changes afterwards, moving along
    `hivemind.supervision.capping.state.TRANSITIONS`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: MessageIdField = Field(description="This proposal's own id.")
    task_id: TaskIdField = Field(description="The task the action serves.")
    cell_id: CellIdField = Field(description="The Cell the action runs on.")
    proposer: WorkerIdField = Field(
        description="The bee proposing; it never verifies its own work."
    )
    risk_tier: RiskTier = Field(
        description="The declared tier; a check may raise it, never lower it."
    )
    action: ProposedAction = Field(
        description="The diff, command or sequence to run (waggle's own shape, carried directly)."
    )
    postconditions: tuple[Postcondition, ...] = Field(
        max_length=MAX_POSTCONDITIONS,
        description="Assertions stated before acting; checked by the gate after applying, never "
        "by the proposer.",
    )
    tempo: Tempo = Field(description="The task's speed-against-accuracy setting.")
    spend_estimate_usd: float = Field(
        default=0.0,
        ge=MIN_SPEND_ESTIMATE_USD,
        description="Expected spend in US dollars; non-zero only for the SPEND tier.",
    )
    clearance: HoneyClearance = Field(description="The label of the action's text.")
    reason: _Reason = Field(description="Why the bee wants to do this.")
    state: ProposalState = Field(
        default=ProposalState.PROPOSED,
        description="Where this proposal is in its lifecycle; PROPOSED for a freshly built one.",
    )
