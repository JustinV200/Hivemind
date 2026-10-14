"""Define PatrolDecision: the one structured value a Patrol's awake episode ever produces.

A Patrol (a watching Warden's hourly review of what it saw on an idle Cell, ADR-0054) is one
stateless awake episode that decides exactly one of four things: raise an Alarm, deposit a Nectar
summary, propose Cell Wax about the Cell, or find nothing notable. `PatrolChoice` is that closed
set, which is all a model is ever offered; `PatrolOutcome` adds `SKIPPED` for the one outcome that
is not a decision at all (no model could be reached, or what it decided could not be delivered),
so a `warden.patrol` event can name every way a Patrol ends. `PatrolDecision` is shaped for
`hivemind.llm.complete_structured`, the way `hivemind.wardens.awake.decision.WardenDecision` is: a
plain, permissive shape a weak model can fill in, with no cross-field validator (the structured
ladder's schema rungs cannot express one), so what a choice needs but did not get is settled by
the caller. It lives here, beside the Patrol that acts on it, rather than with the episode that
asks for it, so this package imports no model machinery: `hivemind.wardens.deps` imports this
package, and an import of `hivemind.llm` or of the awake episode would close a cycle through it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    watch sub-package. Produced by `hivemind.wardens.awake.patrol.decide_patrol`; acted on by
    `hivemind.wardens.watch.patrol.Patrol`. Calls into pydantic only.

Key invariants:
    - `PatrolChoice` has exactly the four members the ADR names; `SKIPPED` is never offered to a
      model, only recorded.
    - A Patrol's wax is never a BLOCK: excluding a Cell from placement is the Queen's decision
      alone, so `PatrolWaxSeverity` offers NOTE and CAUTION only.
    - `reason` and `text` are bounded, and are the model's words about the operator's machine:
      they are `C2` memory, never a trail payload.

See Also:
    - docs/adr/0054-access-levels-watch-mode-and-the-patrol.md for the four outcomes.
    - hivemind.wardens.awake.decision for WardenDecision, the shape this follows.
    - hivemind.wardens.watch.patrol for the Patrol that carries each choice out.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

MAX_REASON_CHARS = 500  # A sentence or two, matching WardenDecision's own bound.
MAX_TEXT_CHARS = 2_000  # A Nectar summary or a Cell Wax caution: a paragraph, never a report.

__all__ = [
    "MAX_REASON_CHARS",
    "MAX_TEXT_CHARS",
    "PatrolChoice",
    "PatrolDecision",
    "PatrolOutcome",
    "PatrolWaxSeverity",
]


class PatrolChoice(Enum):
    """The four things a Patrol may decide; the only values a model is offered."""

    ALARM = "ALARM"  # Something a person should see: a PATROL_ANOMALY Alarm, up the chain.
    NECTAR = "NECTAR"  # Worth keeping, not worth interrupting anyone: a C2 summary as Nectar.
    WAX = "WAX"  # A lasting caution about this Cell: a proposal for Cell Wax, the Queen's to write.
    NOTHING = "NOTHING"  # Nothing notable: the observations the Patrol covered are discarded.


class PatrolOutcome(Enum):
    """How one Patrol ended, as `warden.patrol` records it: a choice, or `SKIPPED`."""

    ALARM = "ALARM"
    NECTAR = "NECTAR"
    WAX = "WAX"
    NOTHING = "NOTHING"
    SKIPPED = "SKIPPED"  # No model was reachable, or the choice could not be delivered: kept.

    @classmethod
    def of(cls, choice: PatrolChoice) -> PatrolOutcome:
        """Return the outcome a decided `choice` ends the Patrol with.

        Args:
            choice: What the Patrol's episode decided.

        Returns:
            The outcome with the same name.
        """
        return cls(choice.value)


class PatrolWaxSeverity(Enum):
    """How much a Patrol's Cell Wax proposal asks to weigh on placement; never a BLOCK."""

    NOTE = "NOTE"  # Recorded, no effect on placement.
    CAUTION = "CAUTION"  # A penalty in placement: a disk nearly full, a service that keeps dying.


class PatrolDecision(BaseModel):
    """One Patrol episode's whole output: which of four things, why, and any text to keep."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    choice: PatrolChoice = Field(description="The one thing this Patrol decided.")
    reason: str = Field(
        max_length=MAX_REASON_CHARS, description="Why, in a sentence or two; kept as the thought."
    )
    text: str = Field(
        default="",
        max_length=MAX_TEXT_CHARS,
        description="For NECTAR the summary to keep, for WAX the caution to propose; otherwise "
        "empty.",
    )
    severity: PatrolWaxSeverity | None = Field(
        default=None, description="For WAX only: NOTE or CAUTION; left empty otherwise."
    )
