"""Define InterventionKind, Thresholds, intervention_kind_for, and a size-capped CompactView.

Roadmap step 4.6: "The Queen watches Warden telemetry and orders `compact` or `handoff` past
thresholds; Wardens do the same to sub-bees." `intervention_kind_for` is the one pure rule behind
both: past `compact_at`, order a compact; past `handoff_threshold`, order a handoff instead
(handoff wins, since a bee that has crossed the harder threshold needs more than an in-place
compact). It lives here, not in `hivemind.supervision` (which is read-only for this dispatch) or in
`hivemind.queen.autopilot`/`hivemind.wardens.autopilot` (an `autopilot/` directory may never import
`hivemind.llm`, codingrules section 4, and this rule must be importable by both `queen/ticks` and
`wardens/ticks` without duplicating it), because `hivemind.memory` sits below both the Queen and
every Warden (Layer 2, beneath Layer 5 and Layer 6) and carries no import restriction of its own
kind. `queen/ticks/context.py` and `wardens/ticks/heartbeat.py` each wrap this module's plain
`InterventionKind` into whatever intervention shape their own layer already speaks (`hivemind.
supervision.Intervention` for the Queen, a wire `waggle.messages.supervision.Intervene` sent
straight to a sub-bee for a Warden). `capped_compact_view` is the other half roadmap step 4.6
requires: "`inspect` returns a compacted view under a size cap" -- every free-text field of the
`waggle.messages.supervision.CompactView` an `inspect()` reply carries is truncated to a cap well
under the wire model's own `MAX_VIEW_CHARS`, with an ellipsis marker on anything cut, so neither a
Queen's nor a Warden's own `compact_view`/`_compact_view` helper can ever hand back something close
to a transcript.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.thresholds`.
    Called by `hivemind.queen.ticks.context.intervention_for` and `hivemind.wardens.ticks.
    heartbeat`'s own sub-bee context check, and by both packages' own `compact_view`/
    `_compact_view` helpers. Calls into waggle only.

Key invariants:
    - `intervention_kind_for` is pure and total: given the same telemetry and thresholds, it
      always returns the same answer, and never raises (a `context_window` of zero cannot reach
      here -- `waggle.messages.supervision.ContextTelemetry.context_window` is validated `>= 1`).
    - `intervention_kind_for` prefers HANDOFF over COMPACT whenever both thresholds are crossed:
      the harder threshold names the more drastic lever, and a bee already past it does not also
      need the milder one first.
    - `capped_compact_view`'s result always satisfies `waggle.messages.supervision.CompactView`'s
      own field and total-size validators, since every cap here is well under that model's own.

See Also:
    - .claude/roadmap.md step 4.6 for the threshold-ordering and size-cap requirements this module
      implements.
    - .claude/codingrules.md section 8.9 for "a bee whose telemetry crosses the manifest threshold
      checkpoints and resets itself; its supervisor may order it earlier".
    - hivemind.queen.ticks.context for intervention_for, the Queen-side wrapper.
    - hivemind.wardens.ticks.heartbeat for compact_view and the Warden-side sub-bee check.
    - waggle.messages.supervision for ContextTelemetry and CompactView, the models this reads from
      and builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from waggle.messages.supervision import CompactView, ContextTelemetry

# The whole capped view's rough ceiling: generous enough to say something useful, far under the
# wire model's own MAX_VIEW_CHARS (8,000) so a compacted view reads as a summary, never a log.
MAX_COMPACT_VIEW_CHARS = 2_000
_GOAL_CAP_CHARS = 300  # One line.
_PROGRESS_CAP_CHARS = 400  # A sentence or two.
_ITEM_CAP_CHARS = 150  # One decision or one open thread, short.
_MAX_ITEMS = 4  # Per section (decisions, open_threads); worst case 300+400+4*150+4*150 <= 2_000.
_ELLIPSIS = "..."  # The truncation marker every capped field uses.

__all__ = [
    "MAX_COMPACT_VIEW_CHARS",
    "InterventionKind",
    "Thresholds",
    "capped_compact_view",
    "fraction_used",
    "intervention_kind_for",
]


class InterventionKind(Enum):
    """Which lever `intervention_kind_for` orders, before its caller builds the real thing."""

    COMPACT = "COMPACT"  # Past compact_at: compact the bee's context in place.
    HANDOFF = "HANDOFF"  # Past handoff_threshold: write a Handoff and stop; a fresh bee resumes.


@dataclass(frozen=True, slots=True)
class Thresholds:
    """The two fractions `intervention_kind_for` compares a bee's context fullness against.

    Attributes:
        compact_at: Fraction of the context window past which a COMPACT is ordered.
        handoff_threshold: Fraction past which a HANDOFF is ordered instead (must be reached
            before COMPACT can ever fire past it, since HANDOFF is checked first).
    """

    compact_at: float
    handoff_threshold: float


def fraction_used(telemetry: ContextTelemetry) -> float:
    """Return how full `telemetry`'s context window is, as a fraction of 1.0.

    Args:
        telemetry: The bee's reported telemetry.

    Returns:
        `tokens_used / context_window`; never raises, since `context_window` is validated `>= 1`
        at the wire boundary.
    """
    return telemetry.tokens_used / telemetry.context_window


def intervention_kind_for(
    telemetry: ContextTelemetry, thresholds: Thresholds
) -> InterventionKind | None:
    """Decide which lever, if any, `telemetry`'s own fullness has earned.

    Args:
        telemetry: The bee's reported telemetry.
        thresholds: The compact and handoff fractions to compare against.

    Returns:
        `InterventionKind.HANDOFF` at or past `thresholds.handoff_threshold`,
        `InterventionKind.COMPACT` at or past `thresholds.compact_at` (but short of handoff),
        `None` below both.
    """
    fraction = fraction_used(telemetry)
    # HANDOFF first: the harder threshold names the more drastic lever, and a bee already past it
    # does not also need the milder COMPACT order first (module docstring).
    if fraction >= thresholds.handoff_threshold:
        return InterventionKind.HANDOFF
    if fraction >= thresholds.compact_at:
        return InterventionKind.COMPACT
    return None


def capped_compact_view(telemetry: ContextTelemetry) -> CompactView:
    """Build a `CompactView` from `telemetry`, every free-text field capped with an ellipsis.

    Args:
        telemetry: The subject's own reported telemetry (`Heartbeat.telemetry` or a sub-bee's own
            `ChildTelemetry.telemetry` row).

    Returns:
        A `CompactView` whose fields are all well under `MAX_COMPACT_VIEW_CHARS` in total
        (roadmap step 4.6: "`inspect` returns a compacted view under a size cap").
    """
    goal = _truncate(telemetry.goal, _GOAL_CAP_CHARS)
    progress = _truncate(
        f"{telemetry.tokens_used}/{telemetry.context_window} tokens used.", _PROGRESS_CAP_CHARS
    )
    decisions = tuple(_truncate(a, _ITEM_CAP_CHARS) for a in telemetry.last_actions[:_MAX_ITEMS])
    open_threads = tuple(_truncate(b, _ITEM_CAP_CHARS) for b in telemetry.blockers[:_MAX_ITEMS])
    return CompactView(goal=goal, progress=progress, decisions=decisions, open_threads=open_threads)


def _truncate(text: str, cap: int) -> str:
    """Cut `text` to `cap` characters, replacing the tail with `_ELLIPSIS` when it was longer."""
    if len(text) <= cap:
        return text
    # Reserve room for the marker itself so the truncated text plus marker never exceeds cap.
    return text[: max(0, cap - len(_ELLIPSIS))] + _ELLIPSIS
