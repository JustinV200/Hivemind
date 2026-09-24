"""Tests for hivemind.llm.embedding.gate: EmbedGate and DirectEmbedGate.

Fits into the Hive:
    Mirrors src/hivemind/llm/embedding/gate.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.embedding.gate for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_bound_embedder, make_embed_request

from hivemind.llm.embedding.fake import FakeEmbedding
from hivemind.llm.embedding.gate import DirectEmbedGate
from hivemind.llm.errors import ProviderUnavailableError
from waggle.clock import FakeClock


async def test_direct_embed_gate_calls_straight_through() -> None:
    provider = FakeEmbedding(clock=FakeClock())
    bound = make_bound_embedder(provider=provider)

    response = await DirectEmbedGate().embed(bound, make_embed_request())

    assert provider.calls == 1
    assert len(response.vectors) == 1


async def test_direct_embed_gate_walks_to_the_fallback_on_provider_unavailable() -> None:
    primary = FakeEmbedding(clock=FakeClock())
    primary.set_available(False)
    fallback_provider = FakeEmbedding(clock=FakeClock())
    fallback = make_bound_embedder(provider=fallback_provider)
    bound = make_bound_embedder(provider=primary, fallback=fallback)

    response = await DirectEmbedGate().embed(bound, make_embed_request())

    assert fallback_provider.calls == 1
    assert len(response.vectors) == 1


async def test_direct_embed_gate_reraises_with_no_fallback_left() -> None:
    provider = FakeEmbedding(clock=FakeClock())
    provider.set_available(False)
    bound = make_bound_embedder(provider=provider)

    with pytest.raises(ProviderUnavailableError):
        await DirectEmbedGate().embed(bound, make_embed_request())
