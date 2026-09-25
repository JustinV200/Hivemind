"""Tests for hivemind.llm.providers.sentence_transformers.embedding: SentenceTransformersEmbedding.

Every test injects a fake `Loader`/`EncoderModel` (codingrules 14.4: fakes over mocks); none of
them ever imports the real `sentence_transformers` package, which this environment does not have
installed (roadmap 7.1's own point: the extra must never be needed to import or test this module).

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/sentence_transformers/embedding.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.sentence_transformers.embedding for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_embed_request

from hivemind.llm.capabilities import HealthState
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.providers.sentence_transformers.embedding import (
    MAX_BATCH,
    EncoderModel,
    SentenceTransformersConfig,
    SentenceTransformersEmbedding,
)
from waggle.clock import FakeClock


class _FakeVectors:
    """A minimal stand-in for the numpy-array-like result `EncoderModel.encode` returns."""

    def __init__(self, rows: list[list[float]]) -> None:
        self._rows = rows

    def tolist(self) -> list[list[float]]:
        return self._rows


class _FakeEncoder:
    """A fake EncoderModel: returns one fixed-length vector per sentence, counting calls."""

    def __init__(self, dimension: int = 3, reported_dimension: int | None = 3) -> None:
        self.dimension = dimension
        self._reported_dimension = reported_dimension
        self.encode_calls = 0
        self.last_normalize: bool | None = None

    def encode(
        self, sentences: list[str], *, batch_size: int, normalize_embeddings: bool
    ) -> _FakeVectors:
        self.encode_calls += 1
        self.last_normalize = normalize_embeddings
        return _FakeVectors([[float(len(s))] * self.dimension for s in sentences])

    def get_sentence_embedding_dimension(self) -> int | None:
        return self._reported_dimension


def _make_provider(
    encoder: EncoderModel | None = None,
    loader_error: Exception | None = None,
    config: SentenceTransformersConfig | None = None,
) -> tuple[SentenceTransformersEmbedding, list[int]]:
    """Build a provider whose loader either returns `encoder` or raises `loader_error`."""
    load_calls = [0]

    def loader(model: str, device: str | None, local_files_only: bool) -> EncoderModel:
        load_calls[0] += 1
        if loader_error is not None:
            raise loader_error
        assert encoder is not None
        return encoder

    cfg = config if config is not None else SentenceTransformersConfig(model="test-model")
    provider = SentenceTransformersEmbedding("p", cfg, FakeClock(), loader=loader)
    return provider, load_calls


# ──────────────────────────────────────────────────────────────────────────────
# embed(): lazy load, batching, dimension
# ──────────────────────────────────────────────────────────────────────────────


async def test_embed_loads_the_model_once_across_two_calls() -> None:
    encoder = _FakeEncoder()
    provider, load_calls = _make_provider(encoder=encoder)

    await provider.embed(make_embed_request(texts=("a",)))
    await provider.embed(make_embed_request(texts=("bb",)))

    assert load_calls[0] == 1  # Loaded lazily, once, never again on the second call.
    assert encoder.encode_calls == 2


async def test_embed_returns_one_vector_per_text_with_the_models_own_dimension() -> None:
    encoder = _FakeEncoder(dimension=5, reported_dimension=5)
    provider, _ = _make_provider(encoder=encoder)

    response = await provider.embed(make_embed_request(texts=("a", "bb")))

    assert response.dimensions == 5
    assert all(len(vector) == 5 for vector in response.vectors)
    assert response.model == "test-model"


async def test_embed_falls_back_to_the_first_vectors_length_when_the_model_reports_none() -> None:
    encoder = _FakeEncoder(dimension=4, reported_dimension=None)
    provider, _ = _make_provider(encoder=encoder)

    response = await provider.embed(make_embed_request(texts=("a",)))

    assert response.dimensions == 4


async def test_embed_passes_normalize_from_config_to_encode() -> None:
    encoder = _FakeEncoder()
    config = SentenceTransformersConfig(model="test-model", normalize=False)
    provider, _ = _make_provider(encoder=encoder, config=config)

    await provider.embed(make_embed_request())

    assert encoder.last_normalize is False


def test_capabilities_reflect_config_normalize_and_a_256_max_batch() -> None:
    provider, _ = _make_provider(encoder=_FakeEncoder())

    assert provider.capabilities.max_batch == MAX_BATCH
    assert provider.capabilities.normalized is True  # SentenceTransformersConfig's own default.


# ──────────────────────────────────────────────────────────────────────────────
# Load failures: ImportError / OSError / ValueError -> ProviderUnavailableError
# ──────────────────────────────────────────────────────────────────────────────


async def test_embed_maps_import_error_to_provider_unavailable_with_the_install_hint() -> None:
    provider, _ = _make_provider(loader_error=ImportError("no module"))

    with pytest.raises(ProviderUnavailableError, match="uv sync --extra embeddings"):
        await provider.embed(make_embed_request())


async def test_embed_maps_os_error_to_provider_unavailable() -> None:
    provider, _ = _make_provider(loader_error=OSError("could not load"))

    with pytest.raises(ProviderUnavailableError):
        await provider.embed(make_embed_request())


async def test_embed_maps_value_error_to_provider_unavailable() -> None:
    provider, _ = _make_provider(loader_error=ValueError("unknown model id"))

    with pytest.raises(ProviderUnavailableError):
        await provider.embed(make_embed_request())


# ──────────────────────────────────────────────────────────────────────────────
# health()
# ──────────────────────────────────────────────────────────────────────────────


async def test_health_reports_healthy_once_the_model_loads() -> None:
    provider, _ = _make_provider(encoder=_FakeEncoder())

    health = await provider.health()

    assert health.state is HealthState.HEALTHY


async def test_health_reports_down_when_the_model_cannot_load() -> None:
    provider, _ = _make_provider(loader_error=ImportError("no module"))

    health = await provider.health()

    assert health.state is HealthState.DOWN
