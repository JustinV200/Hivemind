"""Tests for hivemind.llm.embedding.provider: the EmbeddingProvider protocol.

EmbeddingProvider has no logic of its own (codingrules section 8.1: a Protocol is structural), so
this module covers the two things that belong to `provider.py` itself: that it really is a
non-instantiable Protocol, and that `FakeEmbedding` satisfies its full call surface. The
exhaustive behavioural contract for every implementation lives in `tests/contracts/
test_embedding_provider_contract.py`, not here (mirrors `hivemind.llm.provider`'s own test module).

Fits into the Hive:
    Mirrors src/hivemind/llm/embedding/provider.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.embedding.provider for the module under test.
    - hivemind.llm.embedding.fake for FakeEmbedding, the implementation exercised here.
"""

from __future__ import annotations

import pytest
from builders.llm import make_embed_request

from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.fake import FakeEmbedding
from hivemind.llm.embedding.models import EmbeddingResponse
from hivemind.llm.embedding.provider import EmbeddingProvider
from waggle.clock import FakeClock


def test_embedding_provider_cannot_be_instantiated_directly() -> None:
    # Protocols are structural (codingrules section 8.1): only a real implementation is
    # constructible, never the Protocol class itself.
    with pytest.raises(TypeError):
        EmbeddingProvider()  # type: ignore[misc]  # The instantiation itself is the test.


async def test_fake_embedding_satisfies_the_embedding_provider_protocol() -> None:
    fake = FakeEmbedding(clock=FakeClock())
    # This assignment is itself part of the test: mypy --strict would fail it if FakeEmbedding
    # ever drifted from EmbeddingProvider's shape.
    provider: EmbeddingProvider = fake

    assert isinstance(provider.name, str)
    assert isinstance(provider.capabilities, EmbeddingCapabilities)
    response = await provider.embed(make_embed_request())
    assert isinstance(response, EmbeddingResponse)
    health = await provider.health()
    assert isinstance(health, ProviderHealth)
