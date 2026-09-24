"""Contract suite for EmbeddingProvider: one clause per line, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    `hivemind.llm.embedding.provider.EmbeddingProvider` contract (codingrules 8.6: "same contract
    for every provider", extended to embeddings by ADR-0032) and runs against every implementation
    registered in `_HARNESSES`: `hivemind.llm.embedding.fake.FakeEmbedding`, `hivemind.llm.
    providers.openai_compat.OpenAICompatEmbedding` and `hivemind.llm.providers.
    sentence_transformers.SentenceTransformersEmbedding`, each built by `contracts.
    embedding_provider_harness`'s harness for it. A new embedding adapter passes this suite before
    it is registered in `hivemind.llm.registry.default_embedding_factories` (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/codingrules.md section 8.6 for "same contract for every provider".
    - .claude/codingrules.md section 14.3 for the contract-suite rule this module follows.
    - docs/adr/0032-embedding-provider-and-reembedding-policy.md for the decisions this suite
      proves every embedding adapter honours.
    - contracts.embedding_provider_harness for EmbeddingProviderHarness and every concrete harness.
    - packages/hivemind/tests/contracts/test_llm_provider_contract.py for the chat-side sibling
      this suite's parametrised-harness pattern mirrors.
"""

from __future__ import annotations

import math

import pytest
from builders.llm import make_embed_request
from contracts.embedding_provider_harness import (
    EmbeddingProviderHarness,
    FakeEmbeddingHarness,
    OpenAICompatEmbeddingHarness,
    SentenceTransformersEmbeddingHarness,
)

from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.errors import ProviderUnavailableError

_HARNESSES: dict[str, EmbeddingProviderHarness] = {
    "fake": FakeEmbeddingHarness(),
    "openai_compat": OpenAICompatEmbeddingHarness(),
    "sentence_transformers": SentenceTransformersEmbeddingHarness(),
}


@pytest.fixture(params=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> EmbeddingProviderHarness:
    """One EmbeddingProviderHarness per registered EmbeddingProvider implementation."""
    return _HARNESSES[request.param]


async def test_embed_returns_one_vector_per_text_in_request_order(
    harness: EmbeddingProviderHarness,
) -> None:
    provider = harness.make_provider()
    texts = ("a", "bb", "ccc", "dddd", "e")

    response = await provider.embed(make_embed_request(texts=texts))

    assert len(response.vectors) == len(texts)
    # Every harness's backend maps a text to a vector by content alone, never by position
    # (harness module docstring), so re-embedding the reverse order must reverse the vectors too
    # -- proving the response really is ordered like the request, not merely the right length.
    reordered = await provider.embed(make_embed_request(texts=tuple(reversed(texts))))
    assert reordered.vectors == tuple(reversed(response.vectors))


async def test_dimension_is_stable_and_matches_capabilities_once_known(
    harness: EmbeddingProviderHarness,
) -> None:
    provider = harness.make_provider()

    first = await provider.embed(make_embed_request(texts=("hello",)))
    second = await provider.embed(make_embed_request(texts=("a different, longer text",)))

    assert first.dimensions == second.dimensions
    assert provider.capabilities.dimensions == first.dimensions
    assert all(len(vector) == first.dimensions for vector in first.vectors + second.vectors)


async def test_a_request_wider_than_one_round_trip_still_preserves_order(
    harness: EmbeddingProviderHarness,
) -> None:
    # More texts than any harness's own small per-round-trip batch size (embedding_provider_
    # harness.py's own _OPENAI_BATCH_SIZE = 3), so a batching adapter must stitch several round
    # trips back together in the right order.
    provider = harness.make_provider()
    texts = tuple(f"text-{i}" for i in range(9))

    response = await provider.embed(make_embed_request(texts=texts))

    assert len(response.vectors) == len(texts)
    reordered = await provider.embed(make_embed_request(texts=tuple(reversed(texts))))
    assert reordered.vectors == tuple(reversed(response.vectors))


async def test_usage_is_never_negative_and_carries_no_output_tokens(
    harness: EmbeddingProviderHarness,
) -> None:
    provider = harness.make_provider()

    response = await provider.embed(make_embed_request(texts=("some text", "more text")))

    assert response.usage.input_tokens >= 0
    assert response.usage.output_tokens == 0  # Embedding never generates output tokens.


async def test_an_outage_maps_to_provider_unavailable_error(
    harness: EmbeddingProviderHarness,
) -> None:
    provider = harness.make_provider(outage=True)

    with pytest.raises(ProviderUnavailableError):
        await provider.embed(make_embed_request())


async def test_health_returns_a_reading(harness: EmbeddingProviderHarness) -> None:
    provider = harness.make_provider()

    health = await provider.health()

    assert isinstance(health, ProviderHealth)
    assert health.state in set(HealthState)


async def test_a_normalised_provider_returns_unit_vectors(
    harness: EmbeddingProviderHarness,
) -> None:
    provider = harness.make_provider()
    if not provider.capabilities.normalized:
        pytest.skip("this implementation does not declare normalized vectors")

    response = await provider.embed(make_embed_request(texts=("a reasonably long sentence",)))

    for vector in response.vectors:
        norm = math.sqrt(sum(component * component for component in vector))
        assert norm == pytest.approx(1.0)
