"""Define EmbeddingCapabilities: what one embedding provider can do, declared up front.

Mirrors `hivemind.llm.capabilities.ProviderCapabilities` for the embedding door (codingrules
section 8.6: "capabilities are declared, not assumed"). A caller batching texts, truncating an
overlong one, or deciding whether two vectors can be compared reads this instead of asking which
adapter is behind the call.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.embedding`. Read by
    every caller of `hivemind.llm.embedding.provider.EmbeddingProvider.capabilities`: the Fanner
    (`hivemind.llm.fanner.embed`, batching and metering) and the Honey Store's ripener
    (chunk-sizing against `max_input_chars`, later roadmap step). Calls into nothing beyond
    pydantic.

Key invariants:
    - Frozen and forbids unknown fields (codingrules section 8.5).
    - `dimensions` is `None` until a provider has actually produced a vector (or, for one that
      reports it upfront, from construction); `None` means "unknown", never "zero-length".

See Also:
    - .claude/codingrules.md section 8.6 for "capabilities are declared, not assumed".
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the decision this implements.
    - hivemind.llm.capabilities for ProviderCapabilities, the chat-side sibling this mirrors.
    - hivemind.llm.embedding.provider for EmbeddingProvider.capabilities, the member returning this.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["EmbeddingCapabilities"]


class EmbeddingCapabilities(BaseModel):
    """What one embedding provider can do: its vector length, batching and length limits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimensions: int | None = Field(
        default=None,
        description="The vector length this provider produces, once known; None before the "
        "first successful embed for a provider that only learns it from a response.",
    )
    max_batch: int = Field(
        gt=0, description="The largest number of texts this provider accepts in one call."
    )
    max_input_chars: int = Field(
        gt=0, description="The longest single text this provider accepts before truncating it."
    )
    normalized: bool = Field(
        description="Whether every vector this provider returns is already unit-normalised "
        "(L2 norm 1), so a caller can skip normalising it again before comparing vectors."
    )
