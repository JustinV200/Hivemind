"""Define BoundEmbedder: one EMBEDDER slot resolved to a live provider, a model and a fallback.

Mirrors `hivemind.llm.slots.BoundModel` for the embedding door, with one deliberate difference
ADR-0032 requires: a `fallback` link is only ever built when it serves the *same* model id as the
binding before it, because two different embedding models produce vectors that are not comparable
-- a chat fallback may freely change models (each call carries its own model id), but an embedding
fallback may not. `hivemind.llm.registry.ProviderRegistry.embedder` is the one place that walks a
`[llm.slots]` chain and enforces that rule while building this value; nothing else in the Hive
constructs a `BoundEmbedder` by hand outside of tests.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.embedding`. Built by
    `hivemind.llm.registry.ProviderRegistry.embedder`; read by `hivemind.llm.embedding.gate.
    EmbedGate` implementations and `hivemind.llm.fanner.embed`. Calls into `hivemind.forage.slots`
    (for `ModelSlot`) and this package's own `provider` module only.

Key invariants:
    - `slot` is always `ModelSlot.EMBEDDER` in every value the Hive builds today; carried as a
      field (rather than hard-coded at every call site) so a caller never repeats the enum member.
    - `fallback` is `None` at the end of a chain; a caller walks it by following `.fallback` until
      `None`, exactly like `BoundModel`.
    - Unlike `BoundModel`, this value has no `stamp()`: an `EmbeddingRequest` carries no model id
      to inject (`hivemind.llm.embedding.models`'s own docstring), because the adapter behind
      `provider` is already built for this one `(provider name, model)` pair.

See Also:
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the same-model fallback rule.
    - hivemind.llm.slots for BoundModel, the chat-side value this mirrors.
    - hivemind.llm.registry for ProviderRegistry.embedder, the one production builder of this value.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage.slots import ModelSlot
from hivemind.llm.embedding.provider import EmbeddingProvider

__all__ = ["BoundEmbedder"]


@dataclass(frozen=True, slots=True)
class BoundEmbedder:
    """One EMBEDDER slot resolved to a live provider, a model id, its price and a fallback chain."""

    slot: ModelSlot  # Always ModelSlot.EMBEDDER today.
    binding: str  # The manifest [llm.slots] key that produced this: the slot's own key or a name.
    provider: EmbeddingProvider  # The live provider instance to call.
    model: str  # The model id every vector from `provider` is tagged with (ADR-0032).
    cost_per_million_input_usd: float | None  # Forage map price, or None when unpriced.
    fallback: BoundEmbedder | None = None  # Next binding to try, same model id only; None ends it.
