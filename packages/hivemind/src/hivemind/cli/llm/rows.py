"""Build `hive llm`'s provider and slot rows: each provider's shape and health, each slot's binding.

One row per `[llm.providers.<name>]` (its declared kind, base URL, seats, API key presence and a
live health probe) and one per `hivemind.forage.slots.ModelSlot` (its binding, model, effort,
window, price and fallback chain). A provider with no chat door is probed through the one other
door it serves: an embedding-only kind (roadmap 7.1) through the EMBEDDER slot's own chain, the
transcription-only `whisper_local` (roadmap 6.5a) through the transcriber's. The TRANSCRIBER and
EMBEDDER slots resolve through their own doors (`bound_transcriber`, `embedder`) for the same
reason: a provider there may have no chat factory at all.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm`. Called by
    `hivemind.cli.llm.commands` only. Calls into `hivemind.llm`, `hivemind.forage` and
    `hivemind.manifest` only.

Key invariants:
    - Building a row never raises for a provider or slot the registry cannot serve: offline
      mode's refusal, a kind with no chat door and a transcriber bound to a chat-only kind all
      become a readable cell in the row instead of failing the whole command.

See Also:
    - hivemind.cli.llm.commands for the commands that print these rows.
    - hivemind.llm.registry for the three doors these rows read through.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage import ModelSlot
from hivemind.llm import (
    EMBEDDING_ONLY_KINDS,
    BoundEmbedder,
    BoundModel,
    BoundTranscriber,
    EmbeddingUnsupportedError,
    LLMError,
    OfflineViolationError,
    ProviderRegistry,
    TranscriptionUnsupportedError,
    UnknownProviderError,
)
from hivemind.manifest import HiveManifest, provider_api_key

__all__ = ["ProviderRow", "SlotRow", "provider_rows", "slot_row_for"]


class ProviderRow(BaseModel):
    """One `hive llm providers` row: a provider's shape, key presence and live health."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="The [llm.providers.<name>] key.")
    kind: str = Field(description="Which adapter speaks to this provider.")
    base_url: str = Field(description="The provider's base URL, or '(vendor default)' when hosted.")
    seats: int = Field(description="Concurrent requests this provider allows.")
    has_api_key: bool = Field(description="Whether an API key is set in the environment.")
    health: str = Field(description="The live health probe result, or 'refused: offline'.")


class SlotRow(BaseModel):
    """One `hive llm slots` row: a resolved ModelSlot binding and its fallback chain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: str = Field(description="The ModelSlot's manifest key.")
    binding: str = Field(description="The [llm.slots] key this binding actually resolved.")
    provider: str = Field(description="The provider name serving this binding.")
    model: str = Field(description="The provider's own model id.")
    effort: str = Field(
        description="How hard this binding asks the model to think; '-' for the transcriber and "
        "the embedder."
    )
    context_window: int | None = Field(
        description="This binding's context window, in tokens; None for the transcriber, whose "
        "input is audio, not tokens, and for an embedding binding."
    )
    price: str = Field(description="Per-million-token price, or '-' when the Forage map has none.")
    fallback_chain: str = Field(description="Every binding key in the chain, as 'a -> b -> c'.")


async def provider_rows(
    manifest: HiveManifest, registry: ProviderRegistry
) -> tuple[ProviderRow, ...]:
    """Build one row per configured provider, probing each one's live health in turn."""
    rows = []
    for name in registry.names():
        rows.append(await _one_provider_row(manifest, registry, name))
    return tuple(rows)


async def _one_provider_row(
    manifest: HiveManifest, registry: ProviderRegistry, name: str
) -> ProviderRow:
    """Build one provider's row: its manifest shape, key presence and live health."""
    spec = manifest.llm.providers[name]
    has_key = provider_api_key(name, spec, os.environ) is not None
    return ProviderRow(
        name=name,
        kind=spec.kind,
        base_url=spec.base_url or "(vendor default)",
        seats=spec.seats,
        has_api_key=has_key,
        health=await _probe_health(registry, name, spec.kind),
    )


async def _probe_health(registry: ProviderRegistry, name: str, kind: str) -> str:
    """Construct and probe one provider, or report why offline mode refused it.

    `kind` is the provider's own manifest kind: a kind with no chat door is probed through the
    one other door it serves.
    """
    try:
        provider = registry.provider(name)
    except OfflineViolationError:
        # [llm] offline=true refuses a non-loopback provider at construction (codingrules 8.6);
        # show that refusal instead of letting it fail the whole command.
        return "refused: offline"
    except UnknownProviderError:
        # A configured name the chat registry cannot build serves one other door only: an
        # embedding-only kind (roadmap 7.1) is read through the EMBEDDER slot's own binding, a
        # transcription-only one (whisper_local) through the transcriber chain.
        if kind in EMBEDDING_ONLY_KINDS:
            return await _probe_embedder_health(registry, name)
        return await _probe_transcriber_health(registry, name)
    reading = await provider.health()
    return f"{reading.state.value.lower()}: {reading.detail}"


async def _probe_transcriber_health(registry: ProviderRegistry, name: str) -> str:
    """Probe the transcribers the transcriber slot's chain builds on `name`."""
    try:
        registry.bound_transcriber()  # Builds every link, so each can be probed below.
    except TranscriptionUnsupportedError as exc:
        return f"not probed: {exc}"
    readings = await registry.transcription_health()
    mine = [reading for (provider, _model), reading in readings.items() if provider == name]
    if not mine:
        return "not probed: no slot binds it"
    return "; ".join(f"{reading.state.value.lower()}: {reading.detail}" for reading in mine)


async def _probe_embedder_health(registry: ProviderRegistry, name: str) -> str:
    """Probe `name` through the EMBEDDER chain, or say it serves nothing this Hive binds."""
    try:
        current: BoundEmbedder | None = registry.embedder()
    except LLMError:
        return "embedding-only: not bound to the embedder slot"
    # Walk the chain: `name` may be a same-model fallback rather than the primary link.
    while current is not None:
        if current.provider.name == name:
            reading = await current.provider.health()
            return f"{reading.state.value.lower()}: {reading.detail}"
        current = current.fallback
    return "embedding-only: not bound to the embedder slot"


def slot_row_for(registry: ProviderRegistry, slot: ModelSlot) -> SlotRow:
    """Resolve one slot's row: TRANSCRIBER and EMBEDDER through their own doors, others `bound()`.

    An embedding-only provider (roadmap 7.1's `sentence_transformers`) has no chat door at all, so
    `bound()` cannot resolve the EMBEDDER slot when one serves it; an EMBEDDER bound to a chat-only
    kind is still listed through `bound()`, since the binding exists and the Honey Store merely
    degrades to full-text search on that Hive (ADR-0036).
    """
    if slot is ModelSlot.TRANSCRIBER:
        # Its providers may be transcription-only (whisper_local), which the chat registry cannot
        # construct at all (ADR-0033), so it resolves through its own chain.
        return _transcriber_row(registry)
    if slot is not ModelSlot.EMBEDDER:
        return _slot_row(registry.bound(slot))
    try:
        return _embedder_row(registry.embedder(slot))
    except EmbeddingUnsupportedError:
        return _slot_row(registry.bound(slot))


def _embedder_row(bound: BoundEmbedder) -> SlotRow:
    """Build the EMBEDDER slot's row from a resolved BoundEmbedder (no effort, no window)."""
    keys = []
    current: BoundEmbedder | None = bound
    while current is not None:
        keys.append(current.binding)
        current = current.fallback
    price = bound.cost_per_million_input_usd
    return SlotRow(
        slot=bound.slot.manifest_key,
        binding=bound.binding,
        provider=bound.provider.name,
        model=bound.model,
        effort="-",
        context_window=None,
        price="-" if price is None else f"${price:.2f} per M in",
        fallback_chain=" -> ".join(keys),
    )


def _slot_row(bound: BoundModel) -> SlotRow:
    """Build one `hive llm slots` row from a resolved BoundModel."""
    return SlotRow(
        slot=bound.slot.manifest_key,
        binding=bound.binding,
        provider=bound.provider.name,
        model=bound.model,
        effort=bound.effort.value,
        context_window=bound.context_window,
        price=_format_price(bound),
        fallback_chain=_fallback_chain(bound),
    )


def _transcriber_row(registry: ProviderRegistry) -> SlotRow:
    """Build the transcriber's row from its own chain: no effort, token window or token price."""
    slot = ModelSlot.TRANSCRIBER.manifest_key
    try:
        bound = registry.bound_transcriber()
    except TranscriptionUnsupportedError as exc:
        # A manifest may bind the slot to a chat-only kind until something needs to hear
        # (minimal.toml does); list that plainly rather than failing every other row.
        return SlotRow(
            slot=slot,
            binding=slot,
            provider=exc.provider,
            model="-",
            effort="-",
            context_window=None,
            price="-",
            fallback_chain=f"cannot transcribe ({exc.kind})",
        )
    return SlotRow(
        slot=slot,
        binding=bound.binding,
        provider=bound.provider.name,
        model=bound.model,
        effort="-",
        context_window=None,
        price="-",
        fallback_chain=_transcriber_chain(bound),
    )


def _transcriber_chain(bound: BoundTranscriber) -> str:
    """Walk a transcriber chain's `.fallback` links and join their binding keys."""
    keys = []
    current: BoundTranscriber | None = bound
    while current is not None:
        keys.append(current.binding)
        current = current.fallback
    return " -> ".join(keys)


def _format_price(bound: BoundModel) -> str:
    """Format a binding's per-million-token price, or '-' when the Forage map has none."""
    if bound.cost_per_million_input_usd is None or bound.cost_per_million_output_usd is None:
        return "-"
    return f"${bound.cost_per_million_input_usd:.2f}/${bound.cost_per_million_output_usd:.2f} per M"


def _fallback_chain(bound: BoundModel) -> str:
    """Walk `bound.fallback` and join every link's binding key as 'a -> b -> c'."""
    keys = []
    current: BoundModel | None = bound
    while current is not None:
        keys.append(current.binding)
        current = current.fallback
    return " -> ".join(keys)
