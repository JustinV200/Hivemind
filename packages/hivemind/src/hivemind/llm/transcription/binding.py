"""Define BoundTranscriber and resolve_transcriber: the TRANSCRIBER slot bound to live providers.

`hivemind.llm.slots.BoundModel` binds a chat slot to an `LLMProvider`; a transcription call needs
the same shape around a different door, so `BoundTranscriber` carries the slot, the manifest row
that produced it, a live `TranscriptionProvider`, the model id that row names, its price per
minute of audio and the fallback chain the row names. `resolve_transcriber` builds one by walking
the slot's `[llm.slots]` chain with exactly the rules chat binding uses
(`hivemind.llm.slots.walk_chain`), asking a `TranscriberLookup` for each row's provider. The
lookup is where a provider kind that cannot transcribe is refused, so a manifest binding
`transcriber` to one fails when the slot is bound, not on the first spoken word.

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.transcription`. Called by
    `hivemind.llm.registry.ProviderRegistry.transcriber`, whose lookup builds and caches each
    provider; the result is metered by `hivemind.llm.fanner.MeteredTranscriber`. Imports
    `hivemind.forage` (the slot, the binding rows and the map) and `hivemind.llm.slots` only.

Key invariants:
    - Every row in the chain is looked up, head first, before anything is returned: one
      unusable fallback refuses the whole binding, and the error names the first bad row.
    - `cost_per_audio_minute_usd` is None exactly when there is no map or no map source for the
      row's provider and model: None means unpriced, never free (as for `BoundModel`).
    - `slot` is the same on every link; `binding` is what changes along the chain.

See Also:
    - .claude/codingrules.md section 8.6 for model slots and fallback chains.
    - hivemind.llm.slots for BoundModel and walk_chain, the chat counterparts.
    - hivemind.llm.registry for the production TranscriberLookup.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from hivemind.forage.map import ForageMap, SlotBinding
from hivemind.forage.slots import ModelSlot
from hivemind.llm.slots import walk_chain
from hivemind.llm.transcription.provider import TranscriptionProvider

SECONDS_PER_MINUTE = 60.0  # Audio is priced per minute and measured in seconds.

__all__ = ["SECONDS_PER_MINUTE", "BoundTranscriber", "TranscriberLookup", "resolve_transcriber"]


class TranscriberLookup(Protocol):
    """Build (or fetch) the live transcription provider one `[llm.slots]` row names."""

    def __call__(self, binding: SlotBinding) -> TranscriptionProvider:
        """Return the provider for `binding`'s provider and model.

        Args:
            binding: One row of the chain being bound.

        Returns:
            A TranscriptionProvider serving `binding.model`.

        Raises:
            TranscriptionUnsupportedError: The row's provider kind cannot transcribe.
            UnknownProviderError: The row names no configured provider.
            OfflineViolationError: The Hive is offline and the provider is not local.
        """
        ...


@dataclass(frozen=True, slots=True)
class BoundTranscriber:
    """The TRANSCRIBER slot resolved to a live provider, a model, a price and a fallback chain."""

    slot: ModelSlot  # The slot this chain serves; TRANSCRIBER in practice.
    binding: str  # The [llm.slots] key that produced this link: the slot's own, or a name.
    provider: TranscriptionProvider  # The live provider to call.
    model: str  # The provider's own model id, from the manifest row.
    cost_per_audio_minute_usd: float | None  # The Forage map's price, or None when unpriced.
    fallback: BoundTranscriber | None = None  # The next link to try; None ends the chain.

    def cost_usd(self, audio_s: float) -> float | None:
        """Return what `audio_s` seconds of audio cost on this link, or None when unpriced.

        Args:
            audio_s: Seconds of audio transcribed; >= 0.
        """
        if self.cost_per_audio_minute_usd is None:
            return None
        return audio_s / SECONDS_PER_MINUTE * self.cost_per_audio_minute_usd


def resolve_transcriber(
    slot: ModelSlot,
    bindings: Iterable[SlotBinding],
    lookup: TranscriberLookup,
    map: ForageMap | None = None,
) -> BoundTranscriber:
    """Bind `slot`'s whole `[llm.slots]` chain to transcription providers.

    Args:
        slot: The slot to bind; `ModelSlot.TRANSCRIBER` for every caller today.
        bindings: Every `[llm.slots]` row, forage-side. Consumed once.
        lookup: Builds each row's provider, refusing a kind that cannot transcribe.
        map: Prices each link per audio minute; None leaves every link unpriced.

    Returns:
        The head of the bound chain.

    Raises:
        UnresolvableSlotError: No row for the slot, or its chain cycles.
        TranscriptionUnsupportedError: Some row's provider kind cannot transcribe.
        UnknownProviderError: Some row names no configured provider.
        OfflineViolationError: The Hive is offline and some row's provider is not local.
    """
    chain = walk_chain(slot.manifest_key, bindings)
    # Every provider first, head to tail, so a refusal names the first unusable row and nothing
    # is half-bound when one is refused.
    providers = [lookup(binding) for binding in chain]
    return _link(slot, chain, providers, map, index=0)


def _link(
    slot: ModelSlot,
    chain: Sequence[SlotBinding],
    providers: Sequence[TranscriptionProvider],
    map: ForageMap | None,
    index: int,
) -> BoundTranscriber:
    """Build the link at `index`, recursing for its fallback (the shape `_bind_chain` uses)."""
    fallback = _link(slot, chain, providers, map, index + 1) if index + 1 < len(chain) else None
    binding = chain[index]
    return BoundTranscriber(
        slot=slot,
        binding=binding.key,
        provider=providers[index],
        model=binding.model,
        cost_per_audio_minute_usd=_audio_price(binding, map),
        fallback=fallback,
    )


def _audio_price(binding: SlotBinding, map: ForageMap | None) -> float | None:
    """Return the first matching map source's per-audio-minute price, or None when unpriced."""
    if map is None:
        return None
    source = map.find(binding.provider, binding.model)
    return source.spec.cost.cost_per_audio_minute_usd if source is not None else None
