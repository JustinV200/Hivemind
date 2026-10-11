"""Define SlotBinding, slot_for_binding and ForageMap: every source that can serve a model.

The **Forage map** is every `ModelSource` (`hivemind.forage.models.sources`) the Hive knows about,
wherever it lives: a hosted API, or a server on some Cell (a unit of compute). `ForageMap` owns
that catalogue and its live figures -- distance (measured latency and speed) and abundance (free
seats, and a hosted provider's own measured rate-limit headroom) -- which change on every
measurement and every capacity report, while the static half (`ModelSourceSpec`) an operator wrote
in the manifest never does. `SlotBinding` is the forage-side view of one `[llm.slots]` manifest row
(a key, a provider, a model, an optional fallback key and an effort); `ForageMap.for_slot` resolves
a slot to the map source that row currently names, without this package ever importing
`hivemind.manifest` (Layer 1 may not import Layer 2, codingrules section 4) -- the caller reads the
manifest and builds `SlotBinding`s from it. `slot_for_binding` (roadmap step 10.3) answers the
reverse question over the same rows: which slot a `[llm.slots]` key serves -- itself, when it is a
slot's own key, or the slot whose fallback chain names it (`local_worker` serves `worker` when
`worker`'s chain falls back to it) -- so a rebind's `llm:<slot>` can be checked before it happens.
`add_source` and `remove_source` (roadmap step 8.3) let a Cell's
model server put its models on the map and take them off again at run time: the catalogue is not
fixed at load. `throttle` (roadmap step 4.7a) is the third
live-updating method: it masks a source's headroom to zero after a `RateLimitedError`, until the
window the provider asked for passes (a `RateLimitedError`'s `retry_after_s`, or the Fanner's own
default wait when a provider gives no hint) -- read entirely from the clock, with no timer
anywhere: every read method compares `throttled_until` against the clock itself and returns the
source's real figures again the first time it is read after the window. Routing
(`static_spill_reason`) and the Fanner's seat queueing both stop choosing a masked source.
`set_abundance` (4.7a) closes 3.12a's half-finished loop: it writes both halves of a source's
headroom, free seats and a hosted provider's own reported rate-limit figures, from what the Fanner
measured, never an invented number (a provider that publishes no limits passes None on every
call), and clears any throttle.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Read by `hivemind.forage.allocate`
    (a grant's inputs include a `ForageMap`) and updated by the Fanner (`hivemind.llm.fanner`) as
    it measures speed, free seats and reported rate-limit headroom on every call, and throttles a
    source that just came back rate-limited. Calls into `hivemind.forage.errors`,
    `hivemind.forage.models.sources` and `hivemind.forage.slots` only.

Key invariants:
    - `observe`, `set_abundance`, `throttle`, `add_source` and `remove_source` run under one
      `asyncio.Lock` (codingrules section 8.5's "a class that owns mutable state and says so"):
      each does a read-then-write on one source that must not interleave with another writer's
      write to the same key.
    - `get`, `sources`, `for_slot` and `find` are synchronous and take no lock: a single dict
      lookup or iteration is atomic under the GIL and every `ModelSource` is itself frozen, so a
      reader can never observe a half-updated source, only the whole old one or the whole new one.
      Each still passes its result through `_effective` before returning it, which is itself pure
      and lock-free (a clock comparison and, at most, building one new frozen value), so this
      invariant holds unchanged even though a throttled source's expiry is resolved on every read.
    - Constructing a ForageMap from sources with a repeated `source_id` keeps the last one; this
      mirrors how a manifest's `[forage.map.*]` TOML table itself cannot repeat a key.
      `add_source` follows the same rule: a repeated id replaces the entry, live figures included.
    - `slot_for_binding` never loops on a cyclic table: each chain walk stops at the first key it
      has already seen.

See Also:
    - .claude/roadmap.md step 3.12 for "forage/map.py loads [forage.map.*] and [llm.slots]".
    - .claude/codingrules.md section 8.10 for the Forage map's role in routing and allocation.
    - hivemind.forage.models.sources for ModelSource, Distance and Abundance.
    - hivemind.forage.allocate for grant(), the allocator this map is one input to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage.errors import UnknownSourceError
from hivemind.forage.models.sources import Abundance, Distance, ModelSource
from hivemind.forage.slots import Effort, ModelSlot
from waggle.clock import Clock
from waggle.messages.forage.values import MAX_MODEL_CHARS, MAX_PROVIDER_CHARS

MAX_SLOT_BINDING_KEY_CHARS = 64  # A manifest [llm.slots] key: a slot name or a short named binding.

__all__ = ["ForageMap", "SlotBinding", "slot_for_binding"]

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
    max_output_tokens: int | None = Field(
        default=None,
        gt=0,
        description="The manifest row's own reply-length cap, when it names one; the operator's "
        "knob for a model that thinks past a call site's own budget. None leaves each call "
        "site's budget alone.",
    )


def slot_for_binding(key: str, bindings: Iterable[SlotBinding]) -> ModelSlot | None:
    """Return the slot a `[llm.slots]` key serves: its own, or the one whose chain names it.

    Args:
        key: A manifest `[llm.slots]` key: a slot's own lowercase name, or a named binding.
        bindings: Every `[llm.slots]` row, forage-side.

    Returns:
        The slot `key` names when it is one; otherwise the first slot (in `ModelSlot` order)
        whose fallback chain reaches `key`; None when no slot's chain does, so a caller refuses a
        binding it cannot attribute rather than guessing.
    """
    try:
        return ModelSlot.from_manifest_key(key)
    except KeyError:
        pass  # Not a slot's own key: a named binding, served by whichever chain reaches it.
    by_key = {binding.key: binding for binding in bindings}
    # Walk each slot's own chain from its key; the first chain that reaches `key` names its slot.
    for slot in ModelSlot:
        if key in _chain(slot.manifest_key, by_key):
            return slot
    return None


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
            The matching ModelSource, with any expired throttle already cleared (`_effective`).

        Raises:
            UnknownSourceError: No source with this id is on the map.
        """
        source = self._sources.get(source_id)
        if source is None:
            raise UnknownSourceError(source_id)
        return self._effective(source)

    def sources(self) -> tuple[ModelSource, ...]:
        """Return every source currently on the map, in no particular order.

        Each source's throttle is resolved against the clock first (`_effective`).
        """
        return tuple(self._effective(source) for source in self._sources.values())

    def for_slot(self, slot: ModelSlot, bindings: Iterable[SlotBinding]) -> ModelSource | None:
        """Resolve `slot` to the map source its manifest binding currently names.

        Follows the binding's `fallback` chain (by key, through `bindings`) until a binding names
        a provider and model this map actually has a source for.

        Args:
            slot: The model slot to resolve.
            bindings: Every `[llm.slots]` row, forage-side, keyed by their own `key`.

        Returns:
            The first map source a binding in the chain names (throttle resolved by
            `_effective`), or None when the chain is empty or matches nothing on the map.
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
                return self._effective(match)
            binding = by_key.get(binding.fallback) if binding.fallback is not None else None
        return None

    def find(self, provider: str, model: str) -> ModelSource | None:
        """Return the first source on the map whose spec names `provider` and `model`.

        The Fanner looks a `BoundModel`'s live figures up with this: a binding carries a provider
        name and a model id, not a `source_id` (`static_spill_reason` reads `THROTTLED` off it).

        Args:
            provider: The manifest `[llm.providers.*]` name to match.
            model: The model id to match.

        Returns:
            The first matching ModelSource, with any expired throttle already cleared
            (`_effective`), or None when the map holds no such source.
        """
        match = _find_by_provider_and_model(self._sources.values(), provider, model)
        return self._effective(match) if match is not None else None

    async def add_source(self, source: ModelSource) -> None:
        """Put `source` on the map; a repeated `source_id` replaces the whole entry (roadmap 8.3).

        Args:
            source: The entry to add, such as a model a Cell's Warden just loaded.
        """
        async with self._lock:
            self._sources[source.source_id] = source

    async def remove_source(self, source_id: str) -> bool:
        """Take `source_id` off the map; removing an unknown id is not an error (roadmap 8.3).

        Args:
            source_id: The Forage map entry key to remove.

        Returns:
            True when an entry was removed; False when no source had this id.
        """
        async with self._lock:
            return self._sources.pop(source_id, None) is not None

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

    async def set_abundance(
        self,
        source_id: str,
        seats_free: int,
        requests_per_minute_left: int | None = None,
        tokens_per_minute_left: int | None = None,
    ) -> None:
        """Replace `source_id`'s whole Abundance with what was actually measured this call.

        Writes both halves (free seats and a hosted provider's reported rate-limit headroom) from
        what the Fanner measured, and clears any throttle: a fresh successful call proves the
        source is not throttled any more. See the module docstring (roadmap step 4.7a).

        Args:
            source_id: The source whose abundance changed.
            seats_free: The new count of concurrent requests free on that source.
            requests_per_minute_left: The call's reported requests-per-minute headroom, or None.
            tokens_per_minute_left: The call's reported tokens-per-minute headroom, or None.

        Raises:
            UnknownSourceError: No source with this id is on the map.
        """
        async with self._lock:
            source = self.get(source_id)
            new_abundance = Abundance(
                seats_free=seats_free,
                requests_per_minute_left=requests_per_minute_left,
                tokens_per_minute_left=tokens_per_minute_left,
            )
            self._sources[source_id] = source.model_copy(update={"abundance": new_abundance})

    async def throttle(self, source_id: str, until: datetime) -> None:
        """Zero `source_id`'s headroom until `until`, after a hosted provider rate-limited a call.

        No timer lifts the mask: every read method compares `until` against the clock itself
        (`_effective`). See the module docstring (roadmap step 4.7a).

        Args:
            source_id: The source that was just rate-limited.
            until: The instant this source's headroom should read as free again.

        Raises:
            UnknownSourceError: No source with this id is on the map.
        """
        async with self._lock:
            source = self.get(source_id)
            current = source.abundance
            # Only zero a dimension that was ever reported: an unmetered one (already None) stays
            # None, matching set_abundance's own "never invent a number" rule.
            requests_left = 0 if current.requests_per_minute_left is not None else None
            tokens_left = 0 if current.tokens_per_minute_left is not None else None
            masked = Abundance(
                seats_free=0,
                requests_per_minute_left=requests_left,
                tokens_per_minute_left=tokens_left,
                throttled_until=until,
            )
            self._sources[source_id] = source.model_copy(update={"abundance": masked})

    def _effective(self, source: ModelSource) -> ModelSource:
        """Clear `source`'s throttle if its window has passed, before handing it to a reader.

        Once the clock reaches `throttled_until` the headroom reads as unmeasured again (full
        seats, no rate figures) rather than staying masked forever or being given an invented
        "restored" figure; the next real call re-measures it, like a freshly loaded source.
        """
        until = source.abundance.throttled_until
        if until is None or self._clock.now() < until:
            # Not throttled, or still within its window: return the stored value unchanged.
            return source
        return source.model_copy(update={"abundance": Abundance(seats_free=source.spec.seats)})


def _find_by_provider_and_model(
    sources: Iterable[ModelSource], provider: str, model: str
) -> ModelSource | None:
    """Return the first source in `sources` whose spec names `provider` and `model`, if any."""
    return next((s for s in sources if s.spec.provider == provider and s.spec.model == model), None)


def _chain(start: str, by_key: dict[str, SlotBinding]) -> set[str]:
    """Return every key reachable from `start` by following `fallback`, `start` itself included."""
    seen: set[str] = set()
    current: str | None = start
    # A cycle (which the manifest's own validators forbid) stops at the first repeated key.
    while current is not None and current not in seen and current in by_key:
        seen.add(current)
        current = by_key[current].fallback
    return seen
