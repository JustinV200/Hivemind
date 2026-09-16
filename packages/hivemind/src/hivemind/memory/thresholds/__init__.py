"""The pure compact/handoff threshold rule and a size-capped CompactView builder.

Shared by `hivemind.queen.ticks.context` and `hivemind.wardens.ticks.heartbeat` (roadmap step 4.6),
so the Queen and every Warden order the same lever at the same fullness without either importing
the other's package. See `hivemind.memory.thresholds.rules` for the full explanation.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Calls into waggle only.

Key invariants:
    - None beyond `hivemind.memory.thresholds.rules`'s own; this module only re-exports.

See Also:
    - hivemind.memory.thresholds.rules for InterventionKind, Thresholds, intervention_kind_for,
      capped_compact_view and MAX_COMPACT_VIEW_CHARS.

Public API:
    - InterventionKind: COMPACT or HANDOFF, the two levers the pure rule can order.
    - Thresholds: the compact_at and handoff_threshold fractions the rule compares against.
    - intervention_kind_for: the pure rule itself.
    - fraction_used: how full a bee's context window is, as a fraction of 1.0.
    - capped_compact_view: build a size-capped CompactView from one ContextTelemetry.
    - MAX_COMPACT_VIEW_CHARS: the rough ceiling capped_compact_view's result stays under.
"""

from hivemind.memory.thresholds.rules import (
    MAX_COMPACT_VIEW_CHARS,
    InterventionKind,
    Thresholds,
    capped_compact_view,
    fraction_used,
    intervention_kind_for,
)

__all__ = [
    "MAX_COMPACT_VIEW_CHARS",
    "InterventionKind",
    "Thresholds",
    "capped_compact_view",
    "fraction_used",
    "intervention_kind_for",
]
