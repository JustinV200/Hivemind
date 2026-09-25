"""Define ActOutcome: what carrying out one decision on a Guard request came to.

The Queen's decision on a Guard request (roadmap step 10.6a) is recorded before she acts and
stamped on the request's row after: `ActOutcome` is what the acting part hands back for that stamp
and for the Alarm rule. `outcome` is the one snake_case word the row keeps (`isolated`,
`hive_stand_fallback`, `quarantine_ordered`, `dismissed`, or why it could not be carried out);
`acted` says whether anything was done to a Cell or a bee, which (with a CRITICAL report) is what
puts the report in front of the human; `alarmed` says the acting part already raised that Alarm
itself (an isolation always does), so the decision never raises a second one; `hold` is the
placement hold the Hive Stand's fallback leaves, stored with the decision.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests decision sub-package. Built by `.act` and `.hive_stand`, read by `.decide`.
    Calls into the package's own model only.

Key invariants:
    - Frozen: an outcome is a fact about what happened, never updated.

See Also:
    - hivemind.queen.guard_requests.model for GuardDecision and PlacementHold.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.queen.guard_requests.model import PlacementHold

__all__ = ["ActOutcome"]


@dataclass(frozen=True, slots=True)
class ActOutcome:
    """What carrying out one decision came to.

    Attributes:
        outcome: One snake_case word for the request's row.
        acted: Whether a Cell or a bee was acted on.
        alarmed: Whether a SECURITY Alarm already reached the human for it.
        hold: The placement hold left, if any.
    """

    outcome: str
    acted: bool
    alarmed: bool
    hold: PlacementHold | None = None
