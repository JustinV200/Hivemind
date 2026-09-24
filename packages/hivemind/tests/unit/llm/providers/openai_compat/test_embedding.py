"""Tests for hivemind.llm.providers.openai_compat.embedding: OpenAICompatEmbedding(Config).

Every test builds an `httpx.AsyncClient` over `httpx.MockTransport` (no network), mirroring
`test_provider.py`'s own pattern for this package's chat adapter.

Fits into the Hive:
    Mirrors src/hivemind/llm/providers/openai_compat/embedding.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.providers.openai_compat.embedding for the module under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from builders.llm import make_embed_request
from pydantic import SecretStr

from hivemind.llm.errors import ProviderRequestError
from hivemind.llm.providers.openai_compat.embedding import (
    DEFAULT_MAX_BATCH,
    OpenAICompatEmbedding,
    OpenAICompatEmbeddingConfig,
)
from waggle.clock import FakeClock

BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; no PROVIDER_URL_FRAGMENTS hit.
EMBEDDINGS_PATH = "/v1" + "/embeddings"  # Concatenated: mirrors test_provider.py's own reasoning.
MODELS_PATH = "/v1" + "/models"


def _make_config(**overrides: object) -> OpenAICompatEmbeddingConfig:
    fields: dict[str, object] = {"base_url": BASE_URL, "model": "test-embed", "timeout_s": 5.0}
    fields.update(overrides)
    return OpenAICompatEmbeddingConfig(**fields)


def _make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    config: OpenAICompatEmbeddingConfig | None = None,
) -> OpenAICompatEmbedding:
    cfg = config if config is not None else _make_config()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=cfg.base_url)
    return OpenAICompatEmbedding("p", cfg, http, FakeClock())


def _embeddings_body(vectors: list[list[float]]) -> dict[str, object]:
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {"data": data, "usage": {"prompt_tokens": 7}}


# ──────────────────────────────────────────────────────────────────────────────
# embed(): batching, ordering, dimension tracking
# ──────────────────────────────────────────────────────────────────────────────


async def test_embed_returns_one_vector_per_text_in_order() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embeddings_body([[1.0, 0.0], [0.0, 1.0]]))

    provider = _make_provider(handler)

    response = await provider.embed(make_embed_request(texts=("a", "b")))

    assert response.vectors == ((1.0, 0.0), (0.0, 1.0))
    assert response.dimensions == 2
    assert response.model == "test-embed"
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 0


async def test_embed_sorts_a_reordered_response_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # The server answers out of order; the adapter must still return request order.
        body = {
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ],
            "usage": {"prompt_tokens": 4},
        }
        return httpx.Response(200, json=body)

    provider = _make_provider(handler)

    response = await provider.embed(make_embed_request(texts=("a", "b")))

    assert response.vectors == ((1.0, 0.0), (0.0, 1.0))


async def test_embed_batches_by_the_configured_batch_size() -> None:
    calls: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["input"])
        return httpx.Response(200, json=_embeddings_body([[1.0] for _ in body["input"]]))

    provider = _make_provider(handler, config=_make_config(batch_size=2))

    response = await provider.embed(make_embed_request(texts=("a", "b", "c")))

    assert [len(call) for call in calls] == [2, 1]  # Two texts, then the remaining one.
    assert len(response.vectors) == 3


async def test_embed_truncates_each_text_before_sending() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.extend(body["input"])
        return httpx.Response(200, json=_embeddings_body([[1.0]]))

    provider = _make_provider(handler, config=_make_config(max_input_chars=3))

    await provider.embed(make_embed_request(texts=("abcdefgh",)))

    assert sent == ["abc"]


async def test_embed_refuses_a_later_response_of_a_different_dimension() -> None:
    responses = iter([_embeddings_body([[1.0, 0.0]]), _embeddings_body([[1.0, 0.0, 0.0]])])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    provider = _make_provider(handler, config=_make_config(batch_size=1))

    with pytest.raises(ProviderRequestError, match="dimension"):
        await provider.embed(make_embed_request(texts=("a", "b")))


async def test_embed_refuses_a_reply_with_fewer_vectors_than_texts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embeddings_body([[1.0, 0.0]]))  # One vector, two texts.

    provider = _make_provider(handler)

    # Matching by position would store a vector against the wrong text.
    with pytest.raises(ProviderRequestError, match="1 vectors for 2 texts"):
        await provider.embed(make_embed_request(texts=("a", "b")))


async def test_embed_tolerates_a_non_integer_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"data": [{"index": "zero", "embedding": [1.0, 0.0]}], "usage": {}}
        return httpx.Response(200, json=body)

    provider = _make_provider(handler)

    response = await provider.embed(make_embed_request(texts=("a",)))

    assert response.vectors == ((1.0, 0.0),)


def test_capabilities_report_the_dimension_once_known_and_are_never_normalised() -> None:
    provider = _make_provider(lambda request: httpx.Response(200, json=_embeddings_body([[1.0]])))

    before = provider.capabilities

    assert before.dimensions is None
    assert before.normalized is False
    assert before.max_batch == DEFAULT_MAX_BATCH


# ──────────────────────────────────────────────────────────────────────────────
# create(): bearer header
# ──────────────────────────────────────────────────────────────────────────────


def test_create_adds_a_bearer_header_when_an_api_key_is_set() -> None:
    config = _make_config(api_key=SecretStr("secret-key"))

    provider = OpenAICompatEmbedding.create("p", config, FakeClock())

    assert provider.name == "p"


# ──────────────────────────────────────────────────────────────────────────────
# health()
# ──────────────────────────────────────────────────────────────────────────────


async def test_health_reports_healthy_on_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == MODELS_PATH
        return httpx.Response(200, json={"data": []})

    provider = _make_provider(handler)

    health = await provider.health()

    assert health.detail == "ok"
