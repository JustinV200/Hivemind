"""Walk the EMBEDDER slot's `[llm.slots]` fallback chain, and price one of its bindings.

The EMBEDDER slot (the model binding that turns text into vectors for the Honey Store's search) is
resolved from the manifest's `[llm.slots]` rows like every other slot, one binding and then its
`fallback`, but it is bound by `hivemind.llm.registry.ProviderRegistry.embedder` rather than by
`hivemind.llm.slots.resolve` (ADR-0036: an embedding provider is built per provider and model, and
a fallback is followed only while it serves the same model). These two pure functions are the
parts of that resolution that read only the bindings and the Forage map (the Hive's catalogue of
every source that can serve a model, with its price): `walk_embedder_chain` lists the bindings in
order and refuses a cycle, and `embedder_cost` reads the input-token price one binding's calls
are metered at.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside the `hivemind.llm.embedding`
    package. Called by `hivemind.llm.registry.ProviderRegistry.embedder` and its binder. Calls into
    `hivemind.forage.map` (ForageMap, SlotBinding) and `hivemind.llm.slots`
    (UnresolvableSlotError) only.

Key invariants:
    - Pure: no I/O, no provider is built here; `ProviderRegistry` builds what these name.
    - `walk_embedder_chain` never loops: a revisited key raises instead of walking forever.

See Also:
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the same-model fallback rule.
    - hivemind.llm.slots for the chat slots' own chain walk and price lookup, which these mirror.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.llm.slots import UnresolvableSlotError

__all__ = ["embedder_cost", "walk_embedder_chain"]


def walk_embedder_chain(start: str, by_key: Mapping[str, SlotBinding]) -> list[SlotBinding]:
    """Follow `.fallback` from the binding named `start`, guarding against a cycle.

    Mirrors `hivemind.llm.slots._walk_chain`, kept as its own small copy (rather than imported)
    because that helper is private to `slots.py` and this walk's result feeds the embedding binder,
    not the chat one.

    Args:
        start: The `[llm.slots]` key the walk starts from (`embedder` for the EMBEDDER slot).
        by_key: Every `[llm.slots]` row, keyed by its own key.

    Returns:
        The bindings from `start` onwards, in fallback order; a fallback naming a key with no row
        ends the chain there, the same rule as the chat-side walk.

    Raises:
        UnresolvableSlotError: `start` names no row in `by_key`, or the walk revisits a key.
    """
    if start not in by_key:
        raise UnresolvableSlotError(start)
    chain: list[SlotBinding] = []
    seen: set[str] = set()
    current: str | None = start
    # Each step appends one binding and moves to its fallback, until one is missing or unset.
    while current is not None:
        if current in seen:
            raise UnresolvableSlotError(current, cycle=True)
        seen.add(current)
        binding = by_key.get(current)
        if binding is None:
            break  # A missing fallback target ends the chain, same rule as the chat-side walk.
        chain.append(binding)
        current = binding.fallback
    return chain


def embedder_cost(binding: SlotBinding, map: ForageMap | None) -> float | None:
    """Return the Forage map's input price for `binding`'s provider and model, or None.

    Mirrors `hivemind.llm.slots._cost_from_map`, narrowed to the one price an embedding call
    meters: input tokens only, since an embedding generates no output to price.

    Args:
        binding: One link of the EMBEDDER chain.
        map: The Hive's Forage map, or None where the caller has none (a test, an in-Cell
            registry built before its map).

    Returns:
        The matching source's cost per million input tokens, or None when there is no map or no
        source serves that provider and model.
    """
    if map is None:
        return None
    # The first source serving exactly this provider and model carries the price.
    for source in map.sources():
        if source.spec.provider == binding.provider and source.spec.model == binding.model:
            return source.spec.cost.cost_per_million_input_usd
    return None
