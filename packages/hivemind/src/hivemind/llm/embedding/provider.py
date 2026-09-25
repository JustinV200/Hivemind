"""Define EmbeddingProvider: the one protocol every embed call in the Hive goes through.

Codingrules section 8.6 names this the embedding half of "one door": vendor SDKs and model-server
HTTP clients for embeddings are imported only inside `llm/providers/<name>/`, and everything above
`hivemind.llm` sees only this Protocol plus `hivemind.llm.embedding.models`'s request/response
shapes. Mirrors `hivemind.llm.provider.LLMProvider` in every respect that carries over: a `name`
for identification only (never a branch), declared `capabilities`, and a `health()` reading built
on the same `ProviderHealth` machine as the chat door. What does not carry over is a fallback
inside the call itself: `embed` never walks a chain on its own, because a caller (`hivemind.llm.
embedding.gate.EmbedGate`) decides that, the same separation `LLMProvider.complete` and
`hivemind.llm.ladders.gate.CallGate` already keep.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.llm.embedding`.
    Implemented by `hivemind.llm.providers.openai_compat.embedding.OpenAICompatEmbedding`,
    `hivemind.llm.providers.sentence_transformers.SentenceTransformersEmbedding` and
    `hivemind.llm.embedding.fake.FakeEmbedding`; called through a `hivemind.llm.embedding.bound.
    BoundEmbedder` by `hivemind.llm.embedding.gate.EmbedGate` and `hivemind.llm.fanner.embed`.

Key invariants:
    - `name` exists for logging, the Pheromone Trail and error messages only; never a branch
      (codingrules section 8.6, same rule as `LLMProvider.name`).
    - Every method that can fail typed raises a subclass of `hivemind.llm.errors.LLMError`, never
      a vendor SDK exception: SDK exceptions are caught and translated inside each adapter's own
      `llm/providers/<name>/` package, exactly as the chat door requires.
    - `embed` returns exactly one vector per requested text, in the same order; it never reorders
      or drops one silently.

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this module follows.
    - .claude/codingrules.md section 8.6 for "one door", extended here from chat to embeddings.
    - docs/adr/0036-embedding-provider-and-reembedding-policy.md for the decision this implements.
    - hivemind.llm.provider for LLMProvider, the chat-side door this mirrors.
    - hivemind.llm.embedding.models for EmbeddingRequest and EmbeddingResponse, the types crossing
      this door.
    - hivemind.llm.embedding.fake for FakeEmbedding, the reference implementation used in tests.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.models import EmbeddingRequest, EmbeddingResponse

__all__ = ["EmbeddingProvider"]


class EmbeddingProvider(Protocol):
    """Turn text into vectors: embed a batch, declare capabilities, and report health.

    Implementations must be safe to call concurrently: the Fanner may run several bees' embed
    calls against the same bound provider at once, exactly like `LLMProvider`.
    """

    @property
    def name(self) -> str:
        """Return the manifest's `[llm.providers.<name>]` key for this provider.

        For identification only (logging, the Pheromone Trail, error messages); never branch on
        this value (codingrules section 8.6).
        """
        ...

    @property
    def capabilities(self) -> EmbeddingCapabilities:
        """Return this provider's declared capabilities.

        May change over the provider's lifetime in exactly one way: `dimensions` moves from
        `None` to a concrete value once a real vector has been produced, for an adapter that
        cannot know its output length up front.
        """
        ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed every text in `request`, one vector per text, in order.

        Args:
            request: The texts to embed.

        Returns:
            The completed EmbeddingResponse.

        Raises:
            ProviderUnavailableError: The provider could not be reached, or is refusing all calls.
            RateLimitedError: The provider refused the call due to a rate limit.
            ProviderRequestError: The provider rejected the request on its own terms (a malformed
                batch, or a later response whose dimension disagrees with an earlier one).
        """
        ...

    async def health(self) -> ProviderHealth:
        """Return this provider's current health.

        Returns:
            A fresh ProviderHealth reading; re-probed in memory only, same as `LLMProvider.health`.
        """
        ...
