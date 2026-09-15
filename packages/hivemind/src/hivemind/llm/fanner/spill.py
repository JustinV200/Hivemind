"""Define SpillReason and the pure checks FannerLane.complete uses to decide whether to spill.

Spill-over (codingrules section 8.10) is the Fanner moving a call from its current binding to the
next one in `hivemind.llm.slots.BoundModel.fallback`. Codingrules names three cases -- the current
source's grade is below the calling tempo's floor, the model it names is not loaded there, or
queueing for its seat has already eaten too much of the tempo's latency budget -- and roadmap step
4.7a adds a fourth, checked first: the source is currently throttled, after a hosted provider
rate-limited a call on it (`hivemind.forage.map.ForageMap.throttle`). This module holds the pure,
synchronous half of that decision -- given a source (or none) and a tempo, which reason (if any)
applies -- so `hivemind.llm.fanner.lane.FannerLane.complete` reads as a short walk of these checks
rather than inlining the arithmetic. Nothing here touches a seat, a rate limiter or the trail: it
only ever answers "should this call move on", never performs the move.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.fanner`. Called by
    `hivemind.llm.fanner.lane.FannerLane.complete`. Calls into `hivemind.forage.models` (for
    `ModelSource`) and `hivemind.forage.tempo` (for `Tempo` and `grade_floor`) only.

Key invariants:
    - An unknown source (`source is None`, meaning `hivemind.forage.map.ForageMap.find` found
      nothing for this binding's provider and model) has no grade, abundance or throttle to judge:
      `static_spill_reason` never returns anything but `None` for one -- an unknown source is
      still metered by the lane, just never spilled for any reason (roadmap step 3.12a).
    - "Loaded" is not a field `hivemind.forage.models.sources.ModelSourceSpec` carries (flagged in
      this dispatch's report): `static_spill_reason` reads a source offering zero seats
      (`spec.seats == 0`) as "not serving this model right now", the one meaning `seats` already
      supports without a new manifest field.
    - `THROTTLED` is checked ahead of grade and loaded state: a source `ForageMap.throttle` just
      masked is never also judged on a stale grade or seat figure the mask itself zeroed, and a
      caller reading the reason never has to guess which one "really" applies when both would fire.
    - `queue_wait_exceeded` returns False whenever `tempo.latency_budget_s` is None: with no
      budget there is nothing to measure a queue wait against, so this reason never fires for an
      unbudgeted caller.

See Also:
    - .claude/codingrules.md section 8.10 for the three original spill-over cases this module
      implements; roadmap step 4.7a's own text for the fourth, `THROTTLED`.
    - docs/adr/0015-forage-map-seats-footprints-and-the-fanner.md for the map/Fanner split this
      module's `ModelSource | None` parameter reflects.
    - hivemind.llm.fanner.lane for FannerLane, this module's one caller.
"""

from __future__ import annotations

from enum import Enum

from hivemind.forage.models import ModelSource
from hivemind.forage.tempo import Tempo, grade_floor

SPILL_WAIT_FRACTION = 0.5  # Spill once seat-queue waiting has eaten this fraction of the latency
# budget (roadmap step 3.12a's own constant name and value).
_UNLOADED_SEATS = 0  # A source offering zero seats for a model is read as "not serving it".

__all__ = ["SPILL_WAIT_FRACTION", "SpillReason", "queue_wait_exceeded", "static_spill_reason"]


class SpillReason(Enum):
    """Why FannerLane.complete moved a call to the next binding in its chain (codingrules 8.10)."""

    GRADE_BELOW_FLOOR = "GRADE_BELOW_FLOOR"  # The source's grade is below the tempo's floor.
    MODEL_NOT_LOADED = "MODEL_NOT_LOADED"  # The source offers zero seats for this model.
    QUEUE_WAIT_EXCEEDED = "QUEUE_WAIT_EXCEEDED"  # Seat queueing ate too much of the latency budget.
    THROTTLED = "THROTTLED"  # The source is masked at zero headroom (ForageMap.throttle, 4.7a).


def static_spill_reason(source: ModelSource | None, tempo: Tempo) -> SpillReason | None:
    """Return the spill reason `source`'s own static properties already justify, if any.

    Args:
        source: The current binding's Forage map entry, or None when the map has never heard of
            this (provider, model) pair.
        tempo: The calling lane's speed-against-accuracy setting.

    Returns:
        `THROTTLED`, `GRADE_BELOW_FLOOR` or `MODEL_NOT_LOADED` when `source` is known and fails
        that check, `THROTTLED` taking priority over the other two (see the module docstring's
        "Key invariants"); `None` when `source` is known and clears every check, or when `source`
        is `None` (an unknown source is never spilled for any reason).
    """
    if source is None:
        # Nothing to judge a grade, a loaded state or a throttle against; still metered, though.
        return None
    if source.abundance.throttled_until is not None:
        # ForageMap already resolved an expired throttle before handing this source back
        # (ForageMap._effective), so a non-None value here means the mask is still in force.
        return SpillReason.THROTTLED
    if source.spec.grade < grade_floor(tempo.accuracy):
        return SpillReason.GRADE_BELOW_FLOOR
    if source.spec.seats == _UNLOADED_SEATS:
        return SpillReason.MODEL_NOT_LOADED
    return None


def queue_wait_exceeded(waited_s: float, tempo: Tempo) -> bool:
    """Return whether `waited_s` (a seat queue wait) ate too much of `tempo`'s latency budget.

    Args:
        waited_s: How long `FannerLane.complete` waited to acquire the current binding's seat.
        tempo: The calling lane's tempo; `latency_budget_s` is None when the caller set no budget.

    Returns:
        False when `tempo` has no latency budget (nothing to measure `waited_s` against);
        otherwise whether `waited_s` exceeds `SPILL_WAIT_FRACTION` of the budget.
    """
    if tempo.latency_budget_s is None:
        return False
    return waited_s > SPILL_WAIT_FRACTION * tempo.latency_budget_s
