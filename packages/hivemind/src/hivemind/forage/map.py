"""Define SlotBinding and ForageMap: the catalogue of every source that can serve a model.

The **Forage map** is every `ModelSource` (`hivemind.forage.models.sources`) the Hive knows about,
wherever it lives: a hosted API, or a server on some Cell (a unit of compute). `ForageMap` owns
that catalogue and its live figures -- distance (measured latency and speed) and abundance (free
seats) -- which change on every measurement and every capacity report, while the static half
(`ModelSourceSpec`) an operator wrote in the manifest never does. `SlotBinding` is the forage-side
view of one `[llm.slots]` manifest row (a key, a provider, a model, an optional fallback key and an
effort); `ForageMap.for_slot` resolves a slot to the map source that row currently names, without
this package ever importing `hivemind.manifest` (Layer 1 may not import Layer 2, codingrules
section 4) -- the caller reads the manifest and builds `SlotBinding`s from it.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Read by `hivemind.forage.allocate`
    (a grant's inputs include a `ForageMap`) and updated by the Fanner (`hivemind.llm.fanner`, a
    later phase 3 step) as it measures speed and free seats on every call. Calls into
    `hivemind.forage.errors`, `hivemind.forage.models.sources` and `hivemind.forage.slots` only.

Key invariants:
    - `observe` and `set_abundance` run under one `asyncio.Lock` (codingrules section 8.5's "a
      class that owns mutable state and says so"): each does a read-then-write on one source that
      must not interleave with the other's write to the same key.
    - `get`, `sources`, `for_slot` and `find` are synchronous and take no lock: a single dict
      lookup or iteration is atomic under the GIL and every `ModelSource` is itself frozen, so a
      reader can never observe a half-updated source, only the whole old one or the whole new one.
    - Constructing a ForageMap from sources with a repeated `source_id` keeps the last one; this
      mirrors how a manifest's `[forage.map.*]` TOML table itself cannot repeat a key.

See Also:
    - .claude/roadmap.md step 3.12 for "forage/map.py loads [forage.map.*] and [llm.slots]".
    - .claude/codingrules.md section 8.10 for the Forage map's role in routing and allocation.
    - hivemind.forage.models.sources for ModelSource, Distance and Abundance.
    - hivemind.forage.allocate for grant(), the allocator this map is one input to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage.errors import UnknownSourceError
from hivemind.forage.models.sources import Abundance, Distance, ModelSource
from hivemind.forage.slots import Effort, ModelSlot
from waggle.clock import Clock
from waggle.messages.forage.values import MAX_MODEL_CHARS, MAX_PROVIDER_CHARS

MAX_SLOT_BINDING_KEY_CHARS = 64  # A manifest [llm.slots] key: a slot name or a short named binding.

__all__ = ["ForageMap", "SlotBinding"]

# A frozen, extras-forbidding config, matching every other boundary value (codingrules 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")


class SlotBinding(BaseModel):
    """The forage-side view of one `[llm.slots]` manifest row.

    `hivemind.manifest` owns the row's full validation; this is only the shape `ForageMap.for_slot`
    needs to resolve a slot to a map source, so the map itself never imports the manifest package.
    """

    model_config = _MODEL_CONFIG

    key: Annotated[str, Field(max_length=MAX_SLOT_BINDING_KEY_CHARS)] = Field(
        description="The [llm.slots] key: a lowercase ModelSlot name, or a named binding."
    )
    provider: str = Field(
        max_length=MAX_PROVIDER_CHARS,
        description="The manifest [llm.providers.*] name this row binds to.",
    )
    model: str = Field(max_length=MAX_MODEL_CHARS, description="The model id this row binds to.")
    fallback: Annotated[str, Field(max_length=MAX_SLOT_BINDING_KEY_CHARS)] | None = Field(
        default=None, description="Another [llm.slots] key to fall back to; None for no fallback."
    )
    effort: Effort = Field(description="How hard the model behind this row is asked to think.")


class ForageMap:
    """Own the Forage map's live figures under one lock; the static half never changes after load.

    Constructed once from the manifest's `[forage.map.*]` entries (roadmap step 3.12) and then
    updated in place as the Fanner measures each source, so every caller holding a reference sees
    the latest figures without re-reading the manifest.
    """

    def __init__(self, sources: Iterable[ModelSource], clock: Clock) -> None:
        """Build a map from `sources`, keyed by `source_id`.

        Args:
            sources: The map's starting entries, normally every `[forage.map.*]` row the manifest
                loaded. A repeated `source_id` keeps the last occurrence.
            clock: Injected clock, used only to timestamp `observe`'s `Distance.measured_at`.
        """
        self._clock = clock
        self._sources: dict[str, ModelSource] = {source.source_id: source for source in sources}
        # Guards observe/set_abundance only; see the module docstring for why plain reads do not
        # need it.
        self._lock = asyncio.Lock()

    def get(self, source_id: str) -> ModelSource:
        """Return the source named `source_id`.

        Args:
            source_id: The Forage map entry key to look up.

        Returns:
            The matching ModelSource.

        Raises:
            UnknownSourceError: No source with this id is on the map.
        """
        source = self._sources.get(source_id)
        if source is None:
            raise UnknownSourceError(source_id)
        return source

    def sources(self) -> tuple[ModelSource, ...]:
        """Return every source currently on the map, in no particular order."""
        return tuple(self._sources.values())

    def for_slot(self, slot: ModelSlot, bindings: Iterable[SlotBinding]) -> ModelSource | None:
        """Resolve `slot` to the map source its manifest binding currently names.

        Follows the binding's `fallback` chain (by key, through `bindings`) until a binding names
        a provider and model this map actually has a source for.

        Args:
            slot: The model slot to resolve.
            bindings: Every `[llm.slots]` row, forage-side, keyed by their own `key`.

        Returns:
            The first map source a binding in the chain names, or None if the chain is empty, a
            key is missing, or nothing in the chain matches any source on the map.
        """
        by_key = {binding.key: binding for binding in bindings}
        binding = by_key.get(slot.manifest_key)
        # Walk the fallback chain until a binding's (provider, model) matches a source on the
        # map, or the chain runs out (a missing key ends it, same as no fallback at all).
        while binding is not None:
            match = _find_by_provider_and_model(
                self._sources.values(), binding.provider, binding.model
            )
            if match is not None:
                return match
            binding = by_key.get(binding.fallback) if binding.fallback is not None else None
        return None

    def find(self, provider: str, model: str) -> ModelSource | None:
        """Return the first source on the map whose spec names `provider` and `model`.

        Used by the Fanner (`hivemind.llm.fanner`, roadmap step 3.12a) to look up a
        `BoundModel`'s live figures: a binding carries a provider name and a model id, not a
        `source_id`, so this is how a call resolves the map entry it should meter and, on
        success, update.

        Args:
            provider: The manifest `[llm.providers.*]` name to match.
            model: The model id to match.

        Returns:
            The first matching ModelSource, or None when the map holds no such source.
        """
        return _find_by_provider_and_model(self._sources.values(), provider, model)

    async def observe(self, source_id: str, latency_s: float, tokens_per_s: float) -> None:
        """Record a fresh latency/speed measurement for `source_id`.

        Args:
            source_id: The source that was measured.
            latency_s: Measured round-trip latency, in seconds.
            tokens_per_s: Measured generation speed.

        Raises:
            UnknownSourceError: No source with this id is on the map.
        """
        async with self._lock:
            source = self.get(source_id)
            distance = Distance(
                latency_s=latency_s, tokens_per_s=tokens_per_s, measured_at=self._clock.now()
            )
            self._sources[source_id] = source.model_copy(update={"distance": distance})

    async def set_abundance(self, source_id: str, seats_free: int) -> None:
        """Replace `source_id`'s free-seat figure, keeping its rate-limit headroom unchanged.

        Args:
            source_id: The source whose abundance changed.
            seats_free: The new count of concurrent requests free on that source.

        Raises:
            UnknownSourceError: No source with this id is on the map.
        """
        async with self._lock:
            source = self.get(source_id)
            new_abundance = Abundance(
                seats_free=seats_free,
                requests_per_minute_left=source.abundance.requests_per_minute_left,
                tokens_per_minute_left=source.abundance.tokens_per_minute_left,
            )
            self._sources[source_id] = source.model_copy(update={"abundance": new_abundance})


def _find_by_provider_and_model(
    sources: Iterable[ModelSource], provider: str, model: str
) -> ModelSource | None:
    """Return the first source in `sources` whose spec names `provider` and `model`, if any."""
    return next((s for s in sources if s.spec.provider == provider and s.spec.model == model), None)
