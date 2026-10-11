"""Define RoutingRules and default_floors: the operator's standing orders to the routing policy.

**Routing** (``hivemind.llm.routing``) picks which binding of a slot's fallback chain a bee should
call first. ``RoutingRules`` is everything about that choice the operator, not the task, decides:
each slot's **grade floor** (the weakest Forage-map grade, 1 to 5, a source may have and still be
preferred; ``[llm.floors]`` in the Hive Manifest), whether the Hive runs ``offline`` (no model
outside the Hive may be chosen), the goal's spend cap in US dollars, and a ``raised_floor`` that
a rebind on escalation sets for one bee (a bee whose model failed asks for a stronger one next
time). A task's tempo (how fast it must be done and how right it must be) can raise a floor but
never lower it, and that arithmetic lives in ``hivemind.llm.routing.eligibility``; this module
only holds the standing values and the defaults. ``default_floors`` mirrors the defaults
``hivemind.manifest.schema.floors`` fills into ``[llm.floors]`` (the two packages are independent
Layer 1 siblings that may not import each other, so a test keeps them equal).

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.llm.routing``. Built by
    the composition root (``hivemind.cli.stores.routing_rules``) from the manifest and carried by
    ``RoutingInputs``. Calls into ``hivemind.forage`` (for ``ModelSlot``) only.

Key invariants:
    - ``RoutingRules`` is frozen; a rebind makes a new value with ``raise_floor`` rather than
      editing the Hive's standing rules.
    - The Queen's default floor is 5, the top of the scale, and is NOT capped here: capping it at
      the highest grade actually on the Forage map is a decision-time fact that
      ``hivemind.llm.routing.eligibility.effective_floor`` applies.
    - ``floor_for`` is total: a slot the table omits gets ``DEFAULT_FLOOR``, never a KeyError.

See Also:
    - .claude/roadmap.md step 8.1 for the floors, ``offline``, the spend cap and the raised floor.
    - hivemind.manifest.schema.floors for the manifest-side defaults this mirrors.
    - hivemind.llm.routing.eligibility for how a floor is combined with tempo.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from hivemind.forage import ModelSlot

__all__ = ["DEFAULT_FLOOR", "RoutingRules", "default_floors"]

DEFAULT_FLOOR = 2  # The ordinary slot: a second-rung model or better.
_TOP_GRADE = 5  # The Forage map's strongest grade, which the Queen asks for by default.
_ANYTHING = 1  # The weakest grade: a batch role that accepts whatever model is cheapest.
# The slots whose floor is not DEFAULT_FLOOR, and why: the Queen decides for the whole Hive, and
# the four batch roles run in volume where speed and cost matter more than the last grade point.
_SLOT_FLOORS: Mapping[ModelSlot, int] = {
    ModelSlot.QUEEN: _TOP_GRADE,
    ModelSlot.ATTENDANT: _ANYTHING,
    ModelSlot.RIPENER: _ANYTHING,
    ModelSlot.EMBEDDER: _ANYTHING,
    ModelSlot.TRANSCRIBER: _ANYTHING,
}


def default_floors() -> Mapping[ModelSlot, int]:
    """Return the shipped per-slot grade floors (roadmap step 8.1's decision 2).

    Returns:
        A floor for every ``ModelSlot``: 5 for the Queen (capped at decision time), 1 for the
        Attendant, Ripener, Embedder and Transcriber, and 2 for every other slot.
    """
    return {slot: _SLOT_FLOORS.get(slot, DEFAULT_FLOOR) for slot in ModelSlot}


@dataclass(frozen=True, slots=True)
class RoutingRules:
    """The operator's standing orders to routing: floors, offline, the spend cap, a raised floor."""

    # Each slot's grade floor; build from the manifest with ``LlmSection.grade_floors()``.
    floors: Mapping[ModelSlot, int] = field(default_factory=default_floors)
    offline: bool = False  # True: only a provider that serves from inside the Hive may be chosen.
    # The goal's spend cap in US dollars; 0 means no spending, so only free sources qualify.
    spend_cap_usd: float = 0.0
    raised_floor: int = 0  # A rebind on escalation sets this for one bee; 0 raises nothing.

    def floor_for(self, slot: ModelSlot) -> int:
        """Return ``slot``'s standing grade floor, ``DEFAULT_FLOOR`` when the table omits it."""
        return self.floors.get(slot, DEFAULT_FLOOR)

    def raise_floor(self, grade: int) -> RoutingRules:
        """Return these rules with ``raised_floor`` at least ``grade``; a floor is never lowered.

        Args:
            grade: The grade a rebind on escalation asks for, 1 to 5.

        Returns:
            A copy whose ``raised_floor`` is the larger of the current value and ``grade``.
        """
        return replace(self, raised_floor=max(self.raised_floor, grade))
