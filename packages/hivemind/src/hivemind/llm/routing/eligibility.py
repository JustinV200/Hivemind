"""Define effective_floor and ineligible_cause: which bindings routing may prefer at all.

Before routing ranks the bindings of a slot's chain it asks of each one: may this be chosen?
``effective_floor`` settles the grade bar (a source's grade runs 1 weakest to 5 strongest):
the slot's standing floor from ``[llm.floors]``, raised by a HIGH or CRITICAL accuracy bar and by
a rebind's ``raised_floor``, never lowered by tempo (how fast a task must be done and how right),
and capped at the strongest grade among the sources the slot's own chain names, so the Queen's
default floor of 5 still works on a Hive whose best model is a 4. The cap reads the chain, not the
whole map, because routing can only ever choose a chain row: a grade-5 model bound to another
slot would otherwise hold a Worker chain of grade 3s to a floor none of them can meet, and the
task would fall back to the manifest primary instead of the best of what it can actually reach.
Only when no chain row is on the map at all does the map-wide best stand in, and an empty map
leaves the floor uncapped. ``ineligible_cause`` then checks one binding against
that floor, the capabilities the task needs, the spend cap, the latency budget and ``offline``,
and names the first check it fails. A binding the Forage map does not know (no source for its
provider and model) has no grade, capability list or distance to judge, so it stays eligible
unless offline mode rules its provider out -- the same rule ``static_spill_reason`` applies when
the Fanner decides whether to spill.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.llm.routing``. Called by
    ``hivemind.llm.routing.policy.choose_binding``. Calls into ``hivemind.forage`` (``Tempo``,
    ``AccuracyBar``, ``grade_floor``, ``ModelSource``) and the routing package's own values only.

Key invariants:
    - ``effective_floor`` is monotone: raising ``rules.raised_floor`` or the accuracy bar never
      lowers the result; it is always between 1 and 5 when the map is non-empty.
    - ``effective_floor`` never reads a source outside the slot's chain while any chain row is on
      the map, so adding a strong model to another slot never changes this slot's routing.
    - A LOW or NORMAL accuracy bar never changes the floor: NORMAL means "the role's own floor",
      so the Attendant's floor of 1 is not lifted to NORMAL's 2 by an ordinary task.
    - Every check reads ``ModelSource.effective_grade``, never ``spec.grade`` directly.
    - The checks run in a fixed order (offline, capability, grade, cost, distance) so a binding
      that fails several is always reported for the same one.

See Also:
    - .claude/codingrules.md section 8.14 for "tempo raises a floor, never lowers it".
    - hivemind.llm.fanner.spill.static_spill_reason for the unknown-source rule.
"""

from __future__ import annotations

from hivemind.forage import AccuracyBar, ModelSource, SlotBinding, grade_floor
from hivemind.llm.routing.decision import PassOverCause
from hivemind.llm.routing.inputs import RoutingInputs
from hivemind.llm.slots import UnresolvableSlotError, walk_chain

__all__ = ["effective_floor", "ineligible_cause", "is_free"]

# The accuracy bars that lift a floor above the role's own; LOW and NORMAL leave it alone.
_RAISING_BARS = frozenset({AccuracyBar.HIGH, AccuracyBar.CRITICAL})


def effective_floor(inputs: RoutingInputs) -> int:
    """Return the grade a binding must reach to be preferred for these inputs.

    Args:
        inputs: The routing inputs; the slot, tempo, rules and map sources are read.

    Returns:
        The larger of the slot's standing floor, the accuracy bar's floor (HIGH and CRITICAL
        only) and the rebind's raised floor, capped at the strongest effective grade among the
        sources the slot's chain names (the whole map's when none of them is on it, or when
        ``inputs.bindings`` holds no usable chain for the slot).
    """
    tempo_floor = (
        grade_floor(inputs.tempo.accuracy) if inputs.tempo.accuracy in _RAISING_BARS else 0
    )
    wanted = max(inputs.rules.floor_for(inputs.slot), tempo_floor, inputs.rules.raised_floor)
    if not inputs.sources:
        return wanted  # An empty map has no top to cap at; every binding is unknown anyway.
    # A floor above anything the chain can reach could never be met, which would turn "wants the
    # best" into "wants the impossible"; capping it means the best the chain holds qualifies.
    reachable = _chain_sources(inputs) or inputs.sources
    return min(wanted, max(source.effective_grade for source in reachable))


def _chain_sources(inputs: RoutingInputs) -> tuple[ModelSource, ...]:
    """Return the map sources that some row of the slot's chain names; empty when none is on it."""
    try:
        chain = walk_chain(inputs.slot.manifest_key, inputs.bindings)
    except UnresolvableSlotError:
        # No row for the slot, or a cycle: the floor is still a pure function of the inputs, so
        # it answers map-wide here and leaves refusing the chain to `choose_binding`'s own walk.
        return ()
    named = {(row.provider, row.model) for row in chain}
    return tuple(s for s in inputs.sources if (s.spec.provider, s.spec.model) in named)


def ineligible_cause(
    binding: SlotBinding, source: ModelSource | None, inputs: RoutingInputs, floor: int
) -> PassOverCause | None:
    """Return why ``binding`` may not be chosen, or None when it may.

    Args:
        binding: The ``[llm.slots]`` row being judged.
        source: The Forage map source that row names, or None when the map has none for it.
        inputs: The routing inputs: tempo, required capabilities, rules and local providers.
        floor: The grade floor from ``effective_floor``.

    Returns:
        The first failed check as a ``PassOverCause``, or None when the binding clears them all.
    """
    if inputs.rules.offline and binding.provider not in inputs.local_providers:
        return PassOverCause.NOT_LOCAL_OFFLINE  # Offline: a model outside the Hive is forbidden.
    if source is None:
        return None  # Unknown to Forage: nothing else to judge it on (see the module docstring).
    if not inputs.required_capabilities.issubset(source.spec.capabilities):
        return PassOverCause.CAPABILITY_MISSING
    if source.effective_grade < floor:
        return PassOverCause.GRADE_BELOW_FLOOR
    if inputs.rules.spend_cap_usd <= 0 and not is_free(source):
        return PassOverCause.OVER_COST
    if _too_far(source, inputs):
        return PassOverCause.TOO_FAR
    return None


def is_free(source: ModelSource) -> bool:
    """Return whether calling ``source`` spends no money: no token price, no hosted seat-hour.

    A local source's ``cost_per_seat_hour_usd`` amortises hardware the Hive already owns, so it is
    an accounting figure rather than spend; a hosted source billed by the seat-hour is real spend.
    """
    cost = source.spec.cost
    if cost.cost_per_million_input_usd > 0 or cost.cost_per_million_output_usd > 0:
        return False
    return source.spec.host_cell_id is not None or cost.cost_per_seat_hour_usd <= 0


def _too_far(source: ModelSource, inputs: RoutingInputs) -> bool:
    """Return whether the source's measured latency is past the task's latency budget."""
    budget = inputs.tempo.latency_budget_s
    if budget is None or source.distance is None:
        return False  # No budget, or never measured: nothing to compare (unmeasured is not far).
    return source.distance.latency_s > budget
