"""Define BoundTranscriber and resolve_transcriber: walk ModelSlot.TRANSCRIBER to a live provider.

Code never asks for a transcriber by name; it asks for `ModelSlot.TRANSCRIBER` (the slot that
hears), and the Hive Manifest's `[llm.slots]` table binds that slot to a provider and a model id
with an optional fallback, exactly as it binds every chat slot (codingrules section 8.6).
`BoundTranscriber` is the resolved value -- which provider instance, which model, which manifest
row, and what to try next -- the transcription counterpart of `hivemind.llm.slots.BoundModel`.
`resolve_transcriber` builds one by walking the same forage-side `SlotBinding` rows the chat
resolver walks, with the same chain rules: a missing fallback target ends the chain, and a chain
that revisits a key is refused rather than followed forever. The lookup takes a model id as well
as a provider name, because a transcription provider serves one model per instance (an in-process
Whisper holds exactly one loaded model), so the registry builds one instance per pair.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.transcription`.
    Called by `hivemind.llm.registry.ProviderRegistry.bound_transcriber`, whose `transcriber`
    method is the production `TranscriberLookup`. Calls into `hivemind.forage.map`,
    `hivemind.forage.slots`, `hivemind.llm.slots` (for `UnresolvableSlotError`) and this
    package's `provider` only; never `hivemind.manifest` (codingrules section 4).

Key invariants:
    - `BoundTranscriber` is a frozen, slotted dataclass (codingrules 8.5): an internal value,
      never read from or written to JSON.
    - `slot` is the same on every link of one chain; `binding` is the manifest key per link.
    - `fallback` is None at the end of a chain; callers follow it, never re-resolve mid-call.

See Also:
    - .claude/codingrules.md section 8.6 for "model slots, not model names".
    - docs/adr/0033-transcription-provider-whisper-first.md for "bound like every other slot".
    - hivemind.llm.slots for BoundModel and resolve, the chat counterparts this mirrors.
    - hivemind.forage.map for SlotBinding, the row shape walked here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from hivemind.forage.map import SlotBinding
from hivemind.forage.slots import ModelSlot
from hivemind.llm.slots import UnresolvableSlotError
from hivemind.llm.transcription.provider import TranscriptionProvider

__all__ = ["BoundTranscriber", "TranscriberLookup", "resolve_transcriber"]


class TranscriberLookup(Protocol):
    """Look up the live transcription provider serving one model on one configured provider."""

    def __call__(self, name: str, model: str) -> TranscriptionProvider:
        """Return the provider instance serving `model` on `[llm.providers.<name>]`.

        Args:
            name: A `[llm.providers.<name>]` key.
            model: The model id the binding names.

        Returns:
            A TranscriptionProvider, the same instance on every call for the same pair.

        Raises:
            UnknownProviderError: No provider is configured under `name`.
            TranscriptionUnsupportedError: The provider's kind cannot transcribe.
        """
        ...


@dataclass(frozen=True, slots=True)
class BoundTranscriber:
    """ModelSlot.TRANSCRIBER resolved to a live provider, a model id and a fallback chain."""

    slot: ModelSlot  # The slot this chain serves; TRANSCRIBER for every chain built here.
    binding: str  # The manifest [llm.slots] key that produced this link.
    provider: TranscriptionProvider  # The live provider instance to call.
    model: str  # The model id this link's provider instance serves.
    fallback: BoundTranscriber | None = None  # Next link to try on failure; None ends the chain.


def resolve_transcriber(
    bindings: Iterable[SlotBinding],
    lookup: TranscriberLookup,
    key: str | None = None,
) -> BoundTranscriber:
    """Resolve ModelSlot.TRANSCRIBER (or a named binding) to a BoundTranscriber chain.

    Args:
        bindings: Every `[llm.slots]` row, forage-side (`hivemind.forage.map.SlotBinding`).
        lookup: Returns the live provider for a (provider name, model id) pair.
        key: The `[llm.slots]` key to start from; None starts at the slot's own key
            ("transcriber"). A named binding lets a caller rebind the slot without the manifest.

    Returns:
        The first link, with `.fallback` set for every row the chain reaches.

    Raises:
        UnresolvableSlotError: `bindings` has no row for the starting key, or the chain cycles.
        UnknownProviderError: A row names a provider `lookup` does not know.
        TranscriptionUnsupportedError: A row names a provider whose kind cannot transcribe.
    """
    slot = ModelSlot.TRANSCRIBER
    start = key if key is not None else slot.manifest_key
    chain = _walk_chain(start, {binding.key: binding for binding in bindings})
    # Built from the chain's far end back to its start, so every link is created with its
    # fallback already in hand: the dataclass is frozen and is never patched afterwards.
    bound = _bind_one(slot, chain[-1], lookup, None)
    for row in reversed(chain[:-1]):
        bound = _bind_one(slot, row, lookup, bound)
    return bound


def _walk_chain(start: str, by_key: dict[str, SlotBinding]) -> list[SlotBinding]:
    """Follow `.fallback` from `start`, refusing a missing start and a chain that cycles.

    Raises:
        UnresolvableSlotError: `start` is not in `by_key`, or the walk revisits a key.
    """
    if start not in by_key:
        raise UnresolvableSlotError(start)
    chain: list[SlotBinding] = []
    seen: set[str] = set()
    current: str | None = start
    # Each step appends one row and moves to its fallback; a revisit is a cycle, and a fallback
    # naming no row ends the chain (the chat resolver's own rule, hivemind.llm.slots).
    while current is not None:
        if current in seen:
            raise UnresolvableSlotError(current, cycle=True)
        seen.add(current)
        row = by_key.get(current)
        if row is None:
            break
        chain.append(row)
        current = row.fallback
    return chain


def _bind_one(
    slot: ModelSlot,
    row: SlotBinding,
    lookup: TranscriberLookup,
    fallback: BoundTranscriber | None,
) -> BoundTranscriber:
    """Build one link from `row`, looking its provider up for exactly that row's model."""
    return BoundTranscriber(
        slot=slot,
        binding=row.key,
        provider=lookup(row.provider, row.model),
        model=row.model,
        fallback=fallback,
    )
