"""Define BoundModel and resolve/resolve_key: walk a ModelSlot to a live provider and model.

A `hivemind.forage.slots.ModelSlot` (`QUEEN`, `WORKER`, ...) names *where* a call fits in the
Hive; a `BoundModel` says *how* to actually make that call right now: which `LLMProvider`
instance to use, which of its model ids, at what `Effort`, inside what context window, and what
to try next if this binding fails. `binding` is the manifest key that produced this value -- the
slot's own lowercase name (`"worker"`) or a named binding referenced only from a fallback chain
(`"local_worker"`), per codingrules section 8.6's `[llm.slots]` shape -- so a Pheromone Trail
event or a log line can say which manifest row is in play without re-deriving it from the
provider and model id. `resolve`/`resolve_key` are the two ways to build one: `resolve` starts
from a `ModelSlot`'s own key, `resolve_key` starts from a named binding (a Warden or the Queen
rebinding a slot to `"local_worker"`, say) while still recording which slot the result serves.
Both walk a fallback chain of `hivemind.forage.map.SlotBinding` rows -- the forage-side view of
one `[llm.slots]` manifest row -- rather than the manifest's own `LlmSection`, because codingrules
section 4 fixes `llm` and `manifest` as independent Layer 1 siblings that may not import each
other; a caller (the CLI composition root, roadmap step 3.21) converts a loaded manifest's
`[llm.slots]` table into these rows once, the same way `hivemind.forage.map.ForageMap.for_slot`
already does, for exactly the same reason (see that module's own docstring).
`cost_per_million_input_usd`/`cost_per_million_output_usd` are a copy of the Forage map's prices
taken at bind time, not a live reference: routing and the Fanner (a later roadmap step) meter
spend against these two plain floats, not a live `hivemind.forage.models.ModelCost`.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data). `resolve`/`resolve_key` are called by
    `hivemind.llm.registry.ProviderRegistry` (roadmap step 3.4's other half) and, through it, by
    every Worker, Warden and the Queen wherever codingrules section 8.6 requires a `ModelSlot`
    rather than a model name. Calls into `hivemind.forage.map` (for `SlotBinding` and
    `ForageMap`), `hivemind.forage.slots` (for `ModelSlot` and `Effort`), `hivemind.common.errors`
    and `hivemind.llm.provider` only.

Key invariants:
    - `BoundModel` is frozen and slotted (codingrules section 8.5: "internal values are
      @dataclass(frozen=True, slots=True)"); this is not a boundary model, so it is a dataclass,
      not a pydantic BaseModel -- nothing here is read from or written to JSON/TOML directly.
    - `fallback` is `None` at the end of a chain; a caller walks it by following `.fallback`
      until `None`, never by re-resolving the manifest mid-call.
    - `cost_per_million_input_usd`/`cost_per_million_output_usd` are `None` exactly when `map` is
      `None` or holds no source whose `spec.provider`/`spec.model` match this binding; `None`
      means "unpriced", never "free" (mirrors `hivemind.llm.models.Usage.cost_usd`'s convention).
    - `resolve`/`resolve_key` never loop forever on a cyclic `bindings` table: a bounded walk (a
      `seen`-keys guard) raises `UnresolvableSlotError` instead. The manifest's own `LlmSection`
      validators already reject a cycle before a Hive Manifest loads, so this guard exists for a
      caller that assembles `bindings` by hand, tests included.
    - `slot` is the same `ModelSlot` on every `BoundModel` in one resolved chain, including every
      fallback: it names which slot the whole chain serves. `binding` is what changes per link.

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names" and the fallback rule.
    - .claude/codingrules.md section 4 for why `llm` may depend on `forage` but never `manifest`.
    - docs/adr/0008-llm-provider-independence-and-model-slots.md for the decision this implements.
    - hivemind.forage.map for SlotBinding (the row shape this module walks) and ForageMap.for_slot
      (the same manifest-avoidance pattern, for a single lookup rather than a resolved chain).
    - hivemind.llm.registry for ProviderRegistry, the composition-root-facing caller of both
      resolve() and resolve_key().
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar, Protocol

from hivemind.common.errors import InvariantViolationError
from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.provider import LLMProvider

__all__ = [
    "BoundModel",
    "ProviderLookup",
    "UnresolvableSlotError",
    "resolve",
    "resolve_key",
]


class ProviderLookup(Protocol):
    """Look up a live provider by its manifest `[llm.providers.<name>]` key.

    `hivemind.llm.registry.ProviderRegistry.provider` is the production implementation (lazily
    constructing and caching one instance per name); `resolve`/`resolve_key` take this Protocol
    rather than a registry directly so a test can script one with a plain function or dict lookup.
    """

    def __call__(self, name: str) -> LLMProvider:
        """Return the live provider named `name`.

        Args:
            name: A `[llm.providers.<name>]` key.

        Returns:
            The matching LLMProvider.

        Raises:
            UnknownProviderError: No provider is registered under this name.
        """
        ...


class UnresolvableSlotError(InvariantViolationError):
    """Raise when a fallback chain names a row `bindings` does not have, or cycles back on itself.

    The manifest's own `LlmSection` validators (`hivemind.manifest.schema.llm`) already reject
    both conditions before a Hive Manifest loads, so reaching this in practice means a caller
    built `bindings` some other way (a test, or a future caller assembling one from something
    other than a loaded manifest) -- codingrules section 10's `InvariantViolationError`: "signals
    a bug inside the Hive itself, not bad caller input".
    """

    code: ClassVar[str] = "hivemind.llm.unresolvable_slot"

    def __init__(self, key: str, *, cycle: bool = False) -> None:
        """Build the error for a broken or cyclic fallback chain.

        Args:
            key: The manifest key that could not be resolved: the chain's own starting key when
                nothing names it, or the key the walk revisited when `cycle` is True.
            cycle: True when `key` was seen twice while walking the chain, rather than simply
                being absent from `bindings`.
        """
        reason = "the fallback chain cycles back to it" if cycle else "bindings has no such row"
        super().__init__(f"Cannot resolve [llm.slots] key {key!r}: {reason}.")
        self.key = key


@dataclass(frozen=True, slots=True)
class BoundModel:
    """One ModelSlot resolved to a live provider, a model id, its effort, and a fallback chain.

    See the module docstring for why cost is carried as two plain floats rather than a
    `hivemind.forage` cost type.
    """

    slot: ModelSlot  # The slot this binding serves.
    binding: str  # The manifest [llm.slots] key that produced this: the slot's own key or a name.
    provider: LLMProvider  # The live provider instance to call.
    model: str  # The provider's own model id.
    effort: Effort  # How hard to ask the model to think on this binding.
    context_window: int  # This binding's context window, in tokens (copied at bind time).
    cost_per_million_input_usd: float | None  # Forage map price, or None when unpriced.
    cost_per_million_output_usd: float | None  # Forage map price, or None when unpriced.
    fallback: BoundModel | None = None  # Next binding to try on failure; None ends the chain.


def resolve(
    slot: ModelSlot,
    bindings: Iterable[SlotBinding],
    providers: ProviderLookup,
    map: ForageMap | None = None,
) -> BoundModel:
    """Resolve `slot` to a BoundModel, starting from its own `[llm.slots]` key.

    Args:
        slot: The slot to resolve.
        bindings: Every `[llm.slots]` row, forage-side (see the module docstring for why this is
            not the manifest's own `LlmSection`). Consumed once.
        providers: Looks up a live provider by its manifest name.
        map: The Forage map to price this binding (and every fallback) against; `None` leaves
            every `cost_per_million_*_usd` unpriced.

    Returns:
        A BoundModel for `slot`, with a `.fallback` chain for every row `bindings` lets it reach.

    Raises:
        UnresolvableSlotError: `bindings` has no row for `slot.manifest_key`, or the chain cycles.
        UnknownProviderError: A row in the chain names a provider `providers` does not know.
    """
    return _resolve(slot.manifest_key, slot, _index(bindings), providers, map)


def resolve_key(
    key: str,
    slot: ModelSlot,
    bindings: Iterable[SlotBinding],
    providers: ProviderLookup,
    map: ForageMap | None = None,
) -> BoundModel:
    """Resolve a named binding (`"local_worker"`) as though it were bound to `slot`.

    Used by a rebind (a Warden or the Queen's intervention that moves one slot to a different
    named binding without touching the manifest): the result still names `slot` (it is that
    slot's call, just through a different row), starting from `key` instead of `slot.manifest_key`.

    Args:
        key: The `[llm.slots]` key to start from; need not be `slot.manifest_key`.
        slot: The slot this result is recorded as serving.
        bindings: Every `[llm.slots]` row, forage-side. Consumed once.
        providers: Looks up a live provider by its manifest name.
        map: The Forage map to price this binding (and every fallback) against.

    Returns:
        A BoundModel whose `.slot` is `slot` and whose `.binding` starts at `key`.

    Raises:
        UnresolvableSlotError: `bindings` has no row for `key`, or the chain cycles.
        UnknownProviderError: A row in the chain names a provider `providers` does not know.
    """
    return _resolve(key, slot, _index(bindings), providers, map)


def _index(bindings: Iterable[SlotBinding]) -> dict[str, SlotBinding]:
    """Key every row in `bindings` by its own `.key`, matching `ForageMap.for_slot`'s own build."""
    return {binding.key: binding for binding in bindings}


def _resolve(
    start: str,
    slot: ModelSlot,
    by_key: dict[str, SlotBinding],
    providers: ProviderLookup,
    map: ForageMap | None,
) -> BoundModel:
    """Walk the fallback chain from `start` and bind every row in it, `slot` held constant."""
    chain = _walk_chain(start, by_key)
    return _bind_chain(slot, chain, providers, map, index=0)


def _walk_chain(start: str, by_key: dict[str, SlotBinding]) -> list[SlotBinding]:
    """Follow `.fallback` from `start`, guarding against a cycle with a seen-keys set.

    Raises:
        UnresolvableSlotError: `start` is not in `by_key`, or the walk revisits a key.
    """
    if start not in by_key:
        raise UnresolvableSlotError(start)
    chain: list[SlotBinding] = []
    seen: set[str] = set()
    current: str | None = start
    while current is not None:
        if current in seen:
            raise UnresolvableSlotError(current, cycle=True)
        seen.add(current)
        binding = by_key.get(current)
        if binding is None:
            break  # A missing fallback target ends the chain (ForageMap.for_slot's own rule).
        chain.append(binding)
        current = binding.fallback
    return chain


def _bind_chain(
    slot: ModelSlot,
    chain: list[SlotBinding],
    providers: ProviderLookup,
    map: ForageMap | None,
    index: int,
) -> BoundModel:
    """Build one BoundModel from `chain[index]`, recursing for its fallback.

    Recursive rather than a reversed loop so every branch returns a BoundModel directly, with no
    `fallback: BoundModel | None = None` sentinel for mypy to narrow after the fact.
    """
    fallback = (
        _bind_chain(slot, chain, providers, map, index + 1) if index + 1 < len(chain) else None
    )
    return _bind_one(slot, chain[index], providers, map, fallback)


def _bind_one(
    slot: ModelSlot,
    binding: SlotBinding,
    providers: ProviderLookup,
    map: ForageMap | None,
    fallback: BoundModel | None,
) -> BoundModel:
    """Build one BoundModel from `binding`, pricing it against `map` when one is given."""
    provider = providers(binding.provider)
    cost_input, cost_output = _cost_from_map(binding, map)
    return BoundModel(
        slot=slot,
        binding=binding.key,
        provider=provider,
        model=binding.model,
        effort=binding.effort,
        context_window=provider.capabilities.context_window,
        cost_per_million_input_usd=cost_input,
        cost_per_million_output_usd=cost_output,
        fallback=fallback,
    )


def _cost_from_map(
    binding: SlotBinding, map: ForageMap | None
) -> tuple[float | None, float | None]:
    """Return `(input, output)` per-million-token prices from the first matching map source.

    Returns:
        `(None, None)` when `map` is `None` or holds no source whose `spec.provider`/`spec.model`
        match `binding`; otherwise that source's `ModelCost` fields (which may legitimately be
        `0.0` for a free or local source -- see `hivemind.forage.models.sources.ModelCost`).
    """
    if map is None:
        return None, None
    for source in map.sources():
        if source.spec.provider == binding.provider and source.spec.model == binding.model:
            return (
                source.spec.cost.cost_per_million_input_usd,
                source.spec.cost.cost_per_million_output_usd,
            )
    return None, None
