"""Tests for hivemind.llm.embedding.fake: FakeEmbedding.

Fits into the Hive:
    Mirrors src/hivemind/llm/embedding/fake.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.embedding.fake for the module under test.
"""

from __future__ import annotations

import math

import pytest
from builders.llm import make_embed_request

from hivemind.llm.capabilities import HealthState
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.fake import DEFAULT_DIMENSIONS, FAKE_EMBED_MODEL_ID, FakeEmbedding
from hivemind.llm.errors import ProviderUnavailableError
from waggle.clock import FakeClock


def _norm(vector: tuple[float, ...]) -> float:
    return math.sqrt(sum(component * component for component in vector))


# ──────────────────────────────────────────────────────────────────────────────
# Construction and declared capabilities
# ──────────────────────────────────────────────────────────────────────────────


def test_fake_embedding_defaults_to_normalised_capabilities_and_no_calls() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    assert fake.name == "fake"
    assert fake.capabilities.dimensions == DEFAULT_DIMENSIONS
    assert fake.capabilities.normalized is True
    assert fake.calls == 0


# ──────────────────────────────────────────────────────────────────────────────
# Deterministic hashing
# ──────────────────────────────────────────────────────────────────────────────


async def test_embed_is_deterministic_for_the_same_text() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    first = await fake.embed(make_embed_request(texts=("the quick fox",)))
    second = await fake.embed(make_embed_request(texts=("the quick fox",)))

    assert first.vectors == second.vectors


async def test_embed_differs_for_unrelated_texts() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    response = await fake.embed(make_embed_request(texts=("alpha bravo", "zulu yankee")))

    assert response.vectors[0] != response.vectors[1]


async def test_embed_returns_one_vector_per_text_in_order() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    response = await fake.embed(make_embed_request(texts=("first", "second", "third")))

    assert len(response.vectors) == 3
    assert response.dimensions == DEFAULT_DIMENSIONS
    assert all(len(vector) == DEFAULT_DIMENSIONS for vector in response.vectors)


async def test_embed_returns_unit_normalised_vectors() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    response = await fake.embed(make_embed_request(texts=("some ordinary sentence",)))

    assert _norm(response.vectors[0]) == pytest.approx(1.0)


async def test_a_text_with_no_word_token_maps_to_a_fixed_unit_vector() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    response = await fake.embed(make_embed_request(texts=("!!!", "...")))

    # Neither text has an [a-z0-9]+ token, so both fall back to the same fixed sentinel vector.
    assert response.vectors[0] == response.vectors[1]
    assert _norm(response.vectors[0]) == pytest.approx(1.0)


async def test_embed_lowercases_before_hashing() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    response = await fake.embed(make_embed_request(texts=("Hello World", "hello world")))

    assert response.vectors[0] == response.vectors[1]


async def test_embed_uses_the_neutral_fake_model_id() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    response = await fake.embed(make_embed_request())

    assert response.model == FAKE_EMBED_MODEL_ID


async def test_embed_usage_is_a_chars_over_four_ceiling_with_zero_output() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    response = await fake.embed(make_embed_request(texts=("abcdefghij",)))  # 10 chars

    assert response.usage.input_tokens == 3  # ceil(10 / 4)
    assert response.usage.output_tokens == 0
    assert response.usage.cost_usd is None


# ──────────────────────────────────────────────────────────────────────────────
# calls / set_available / health
# ──────────────────────────────────────────────────────────────────────────────


async def test_calls_counts_every_embed_call() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    await fake.embed(make_embed_request())
    await fake.embed(make_embed_request())

    assert fake.calls == 2


async def test_set_available_false_makes_embed_raise() -> None:
    fake = FakeEmbedding(clock=FakeClock())
    fake.set_available(False)

    with pytest.raises(ProviderUnavailableError):
        await fake.embed(make_embed_request())
    assert fake.calls == 0  # The outage is checked before a call is counted.


async def test_health_reports_down_while_unavailable_and_healthy_otherwise() -> None:
    fake = FakeEmbedding(clock=FakeClock())

    healthy = await fake.health()
    fake.set_available(False)
    down = await fake.health()

    assert healthy.state is HealthState.HEALTHY
    assert down.state is HealthState.DOWN


def test_custom_capabilities_are_kept_as_given() -> None:
    caps = EmbeddingCapabilities(dimensions=16, max_batch=10, max_input_chars=100, normalized=False)

    fake = FakeEmbedding(clock=FakeClock(), capabilities=caps)

    assert fake.capabilities == caps
