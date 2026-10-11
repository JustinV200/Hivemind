"""Define RoutingInputs: everything ``choose_binding`` reads, gathered into one frozen value.

Routing (picking which binding of a slot's fallback chain a bee calls first) is a pure function,
so every fact it weighs arrives as data in one ``RoutingInputs``: the slot, the task's tempo (how
fast it must be done and how right it must be), the capabilities the task needs (names from
``ModelSourceSpec.capabilities`` such as ``vision``), a snapshot of the Forage map's sources, the
Cell (a unit of compute) the bee runs on, that Cell's hosting plan (the Queen's per-slot choice
of primary source and fallbacks) when one has been written, the ``[llm.slots]`` rows that make up
the chain, which providers serve from inside the Hive, and the operator's ``RoutingRules``. The
map arrives as a tuple of sources rather than a live ``ForageMap`` so the decision is a function
of its arguments alone: no lock, no clock, no I/O. A caller takes ``ForageMap.sources()`` once per
decision; ``RoutingInputs.from_map`` does exactly that.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside ``hivemind.llm.routing``. Built by
    the Warden that spawns or rebinds a bee and by the Queen for her own awake episodes; consumed
    by ``hivemind.llm.routing.policy.choose_binding``. Calls into ``hivemind.forage`` and
    ``waggle.ids`` only.

Key invariants:
    - Frozen: one decision never sees a map that changed under it.
    - ``required_capabilities`` holds capability flag names, not provider capabilities: it is
      compared against ``ModelSourceSpec.capabilities`` and nothing else.
    - ``cell_id`` None means the bee's Cell is unknown (the Queen on the Hive Stand before any
      Cell is chosen), so no source counts as local to it.

See Also:
    - hivemind.llm.routing.policy for the function that reads this.
    - hivemind.llm.routing.rules for RoutingRules.
    - hivemind.forage.map for ForageMap and SlotBinding.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage import ForageMap, HostingPlan, ModelSlot, ModelSource, SlotBinding, Tempo
from hivemind.llm.routing.rules import RoutingRules
from waggle.ids import CellId

__all__ = ["RoutingInputs"]


@dataclass(frozen=True, slots=True)
class RoutingInputs:
    """Every fact ``choose_binding`` weighs, so the decision is a pure function of this value."""

    slot: ModelSlot  # The slot the bee's calls serve; its chain is walked from its own key.
    tempo: Tempo  # The task's accuracy bar and optional latency budget.
    required_capabilities: frozenset[str]  # Flags the source must have, e.g. "vision".
    sources: tuple[ModelSource, ...]  # A snapshot of the Forage map (`ForageMap.sources()`).
    bindings: tuple[SlotBinding, ...]  # Every `[llm.slots]` row, forage-side.
    rules: RoutingRules  # Floors, offline, the spend cap and any raised floor.
    cell_id: CellId | None = None  # The Cell the bee runs on; None when not yet placed.
    hosting: HostingPlan | None = None  # That Cell's hosting plan, when the Queen wrote one.
    # The `[llm.providers]` names that serve from inside the Hive (in process, loopback or the
    # Hive Stand); offline routing accepts only these.
    local_providers: frozenset[str] = frozenset()

    @classmethod
    def from_map(
        cls,
        forage: ForageMap,
        slot: ModelSlot,
        tempo: Tempo,
        bindings: tuple[SlotBinding, ...],
        rules: RoutingRules,
    ) -> RoutingInputs:
        """Build inputs from a live map, taking its sources once.

        Args:
            forage: The Forage map to snapshot; throttles that have expired are already cleared.
            slot: The slot the bee's calls serve.
            tempo: The task's tempo.
            bindings: Every `[llm.slots]` row, forage-side.
            rules: The operator's standing rules.

        Returns:
            Inputs with no required capabilities, no Cell and no hosting plan; a caller adds
            those with ``dataclasses.replace``.
        """
        return cls(
            slot=slot,
            tempo=tempo,
            required_capabilities=frozenset(),
            sources=forage.sources(),
            bindings=bindings,
            rules=rules,
        )
