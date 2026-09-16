"""Define intervention_for: wrap the pure compact/handoff threshold rule into a real Intervention.

Roadmap step 4.6: "The Queen watches Warden telemetry and orders `compact` or `handoff` past
thresholds." `hivemind.memory.thresholds.intervention_kind_for` is the pure rule shared with a
Warden's own sub-bee check (`hivemind.wardens.ticks.heartbeat`); this module is the thin, Queen-side
wire from that rule to a real `hivemind.supervision.Intervention` (`Compact`/`Handoff`), the value
`hivemind.queen.queen.Queen.intervene`'s own wire-sending path (`_send_intervene`) already knows how
to convert and dispatch. It stays this small on purpose: `hivemind.queen.ticks.liveness` is the one
caller, and it already holds the Warden link and the `MemoryBudget` thresholds a plain function
needs, so there is nothing here to wrap in a class.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.ticks.liveness.handle_heartbeat_item`, once per received
    Heartbeat, against the sending Warden's own reported `ContextTelemetry`. Calls into
    `hivemind.memory.thresholds` (InterventionKind, Thresholds, intervention_kind_for) and
    `hivemind.supervision` (Compact, Handoff, Intervention) only.

Key invariants:
    - `intervention_for` is pure: given the same telemetry and thresholds, it always returns the
      same answer (or lack of one), mirroring `intervention_kind_for`'s own purity one layer up.
    - The `reason` on every `Intervention` this builds names the fraction crossed and which
      threshold, so the Pheromone Trail event `hivemind.queen.ticks.liveness` records from it (via
      `hivemind.queen.trail.record_forage_event`'s sibling for `queen.*` kinds) is self-explanatory
      without a second lookup.

See Also:
    - .claude/roadmap.md step 4.6 for the threshold-ordering requirement this module wires up.
    - hivemind.memory.thresholds for the pure rule this module wraps.
    - hivemind.queen.ticks.liveness for the one caller.
    - hivemind.wardens.ticks.heartbeat for the Warden-side mirror of this same rule.
"""

from __future__ import annotations

from hivemind.memory.thresholds import InterventionKind, Thresholds, intervention_kind_for
from hivemind.supervision import Compact, Handoff, Intervention
from waggle.messages.supervision import ContextTelemetry

__all__ = ["intervention_for"]


def intervention_for(telemetry: ContextTelemetry, thresholds: Thresholds) -> Intervention | None:
    """Decide, and build, the Intervention (if any) `telemetry`'s own fullness has earned.

    Args:
        telemetry: The Warden's own reported `ContextTelemetry` (`Heartbeat.telemetry`).
        thresholds: The compact and handoff fractions to compare against
            (`hivemind.queen.deps.MemoryBudget.compact_at`/`.handoff_threshold`).

    Returns:
        `hivemind.supervision.Handoff` past `thresholds.handoff_threshold`,
        `hivemind.supervision.Compact` past `thresholds.compact_at` (but short of handoff), or
        `None` below both.
    """
    kind = intervention_kind_for(telemetry, thresholds)
    fraction = telemetry.tokens_used / telemetry.context_window
    if kind is InterventionKind.HANDOFF:
        return Handoff(reason=_reason("handoff_threshold", thresholds.handoff_threshold, fraction))
    if kind is InterventionKind.COMPACT:
        return Compact(reason=_reason("compact_at", thresholds.compact_at, fraction))
    return None


def _reason(threshold_name: str, threshold: float, fraction: float) -> str:
    """Build the one-line reason every Intervention this module builds carries."""
    return (
        f"Context is {fraction:.0%} full, at or past {threshold_name}={threshold:.0%}: "
        "the Queen orders this to keep the bee's window from overflowing."
    )
