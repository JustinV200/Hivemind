"""Define EmbedGate: the one seam every embed call passes through, mirroring chat's CallGate.

A caller never calls `bound.provider.embed` itself (`hivemind.llm.ladders.gate`'s module docstring
states the same rule for chat). It calls an `EmbedGate` instead. `DirectEmbedGate` is the unmetered
default, used directly by callers that need no seat accounting (`hive llm test embedder`) and as
the shape `hivemind.llm.fanner.embed` metering wraps once the Fanner is wired in. Unlike `CallGate`,
`DirectEmbedGate` does walk `bound.fallback` on its own, on exactly one error:
`ProviderUnavailableError`. This is safe here in a way it is not for chat: `hivemind.llm.embedding.
bound.BoundEmbedder.fallback` exists only when `hivemind.llm.registry.ProviderRegistry.embedder`
already proved it serves the same model id (ADR-0036), so walking it can never silently mix vector
spaces the way spilling a chat call to a different model could.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.embedding`. Called by
    `hive llm test embedder` (`hivemind.cli.llm`) directly; implemented a second way, with
    metering, by `hivemind.llm.fanner.embed` behind `hivemind.llm.fanner.lane.FannerLane.embed`.
    Calls into `hivemind.llm.errors`, `hivemind.llm.embedding.bound` and `hivemind.llm.embedding.
    models` only.

Key invariants:
    - `DirectEmbedGate.embed` walks `bound.fallback` on `ProviderUnavailableError` only: a
      `RateLimitedError` or a `ProviderRequestError` propagates unchanged, because those mean the
      provider answered (a "wait" or a "this request is broken"), not "try somewhere else".
    - An `EmbedGate` never decides *whether* a fallback link exists, only whether to use one that
      already does; that decision belongs entirely to whatever built the `BoundEmbedder`.

See Also:
    - .claude/codingrules.md section 8.6 for "degrade by ladder, in one place", applied here to
      the narrower same-model fallback rule ADR-0036 sets for embeddings.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the same-model fallback rule.
    - hivemind.llm.ladders.gate for CallGate, the chat-side seam this mirrors.
    - hivemind.llm.embedding.bound for BoundEmbedder, the value every EmbedGate call is made
      through.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.llm.embedding.bound import BoundEmbedder
from hivemind.llm.embedding.models import EmbeddingRequest, EmbeddingResponse
from hivemind.llm.errors import ProviderUnavailableError

__all__ = ["DirectEmbedGate", "EmbedGate"]


class EmbedGate(Protocol):
    """Make one embed call through a BoundEmbedder; the seam a metering layer can sit behind."""

    async def embed(self, bound: BoundEmbedder, request: EmbeddingRequest) -> EmbeddingResponse:
        """Run `request` against `bound` and return the completed response.

        Args:
            bound: The binding to call; `bound.provider` and `bound.model` decide who answers.
            request: The texts to embed.

        Returns:
            The completed EmbeddingResponse.

        Raises:
            RateLimitedError: The provider refused the call due to a rate limit.
            ProviderUnavailableError: The provider (and every same-model fallback, if any) could
                not be reached.
            ProviderRequestError: The provider rejected the request on its own terms.
        """
        ...


class DirectEmbedGate:
    """Call `bound.provider.embed`, walking to `bound.fallback` on `ProviderUnavailableError`."""

    async def embed(self, bound: BoundEmbedder, request: EmbeddingRequest) -> EmbeddingResponse:
        """Call straight through to the provider, trying each same-model fallback in turn.

        See `EmbedGate.embed` for the full contract.
        """
        current = bound
        while True:
            try:
                # External await: the actual embedding call; the provider adapter owns its own
                # timeout (codingrules section 11).
                return await current.provider.embed(request)
            except ProviderUnavailableError:
                if current.fallback is None:
                    raise  # Nowhere left to try; the caller sees the same error it would have.
                current = current.fallback  # Same model id, guaranteed by how this chain was built.
