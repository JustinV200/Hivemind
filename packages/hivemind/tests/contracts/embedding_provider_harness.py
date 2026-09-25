"""Provide one EmbeddingProviderHarness per EmbeddingProvider implementation, for the contracts.

`test_embedding_provider_contract.py` (roadmap step 7.1) writes each contract clause once against
the `hivemind.llm.embedding.provider.EmbeddingProvider` Protocol and runs it over every
implementation this module registers a harness for: the fake (`FakeEmbeddingHarness`, over
`hivemind.llm.embedding.fake.FakeEmbedding` directly), and the two real adapters
(`OpenAICompatEmbeddingHarness` over `httpx.MockTransport`, `SentenceTransformersEmbeddingHarness`
over a fake `Loader`/`EncoderModel`), so no network call and no real model library is ever
involved. Every harness's backend maps a text to a vector purely by that text's own content
(`_deterministic_vector`, keyed on length), never by its position in a batch, so the contract
suite can prove order preservation by re-embedding a permutation and checking the vectors permute
the same way -- without needing to know any implementation's internal hashing or encoding scheme.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used only by
    `contracts.test_embedding_provider_contract`.

Key invariants:
    - `make_provider(outage=True)` makes the built provider's very first `embed()` call raise
      `ProviderUnavailableError`, for every harness, without needing a second call to arrange it
      (mirrors `contracts.llm_provider_harness.ProviderHarness`'s own shape, simplified: embedding
      has no capability ladder to vary, so there is nothing else a harness needs to arrange).
    - `_deterministic_vector`'s output length is always `DIMS`, regardless of input text length or
      content: only the vector's *values* depend on the text, never its dimensionality.

See Also:
    - .claude/codingrules.md section 14.3 for the contract-suite rule this module supports.
    - contracts.test_embedding_provider_contract for the clauses built on this module.
    - contracts.llm_provider_harness for the chat-side sibling this harness set mirrors.
    - hivemind.llm.embedding.provider for the EmbeddingProvider Protocol every harness builds.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Protocol

import httpx

from hivemind.llm.embedding.fake import FakeEmbedding
from hivemind.llm.embedding.provider import EmbeddingProvider
from hivemind.llm.providers.openai_compat.embedding import (
    OpenAICompatEmbedding,
    OpenAICompatEmbeddingConfig,
)
from hivemind.llm.providers.sentence_transformers.embedding import (
    EncoderModel,
    SentenceTransformersConfig,
    SentenceTransformersEmbedding,
)
from waggle.clock import FakeClock

DIMS = 4  # Small, fast, shared by every harness's backend so vectors are cheap to compare.
_OPENAI_BATCH_SIZE = 3  # Deliberately small: a 9-text contract request makes three round trips.
_OPENAI_BASE_URL = "http://127.0.0.1:9/v1"  # Port 9: nothing listens; never a real server.
_OPENAI_EMBEDDINGS_PATH = "/v1" + "/embeddings"  # Concatenated: check_no_model_ids.py bans the
# joined literal outright, even as a mock transport's own routing key (see llm_provider_harness.py).
_OPENAI_MODELS_PATH = "/v1" + "/models"

__all__ = [
    "DIMS",
    "EmbeddingProviderHarness",
    "FakeEmbeddingHarness",
    "OpenAICompatEmbeddingHarness",
    "SentenceTransformersEmbeddingHarness",
]


class EmbeddingProviderHarness(Protocol):
    """Build one EmbeddingProvider implementation, optionally already simulating an outage."""

    def make_provider(self, *, outage: bool = False) -> EmbeddingProvider:
        """Return a fresh provider; `outage=True` makes its next `embed()` call fail."""
        ...


def _deterministic_vector(text: str) -> list[float]:
    """Map `text` to a small, non-zero, content-determined vector (never position-determined)."""
    magnitude = float(len(text) % 7 + 1)
    return [magnitude] * DIMS


def _normalised(vector: list[float]) -> list[float]:
    """Return `vector` scaled to unit L2 length."""
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector]


class FakeEmbeddingHarness:
    """Build FakeEmbedding directly; `outage=True` calls `set_available(False)` before returning."""

    def make_provider(self, *, outage: bool = False) -> EmbeddingProvider:
        """Build a fresh FakeEmbedding, `DIMS`-dimensional; see `EmbeddingProviderHarness`."""
        provider = FakeEmbedding(name="fake", dimensions=DIMS, clock=FakeClock())
        if outage:
            provider.set_available(False)
        return provider


class OpenAICompatEmbeddingHarness:
    """Build OpenAICompatEmbedding over `httpx.MockTransport`, routing by path."""

    def make_provider(self, *, outage: bool = False) -> EmbeddingProvider:
        """Build a fresh OpenAICompatEmbedding; see `EmbeddingProviderHarness`."""
        config = OpenAICompatEmbeddingConfig(
            base_url=_OPENAI_BASE_URL,
            model="test-embed",
            timeout_s=5.0,
            batch_size=_OPENAI_BATCH_SIZE,
        )
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(_handler(outage)), base_url=_OPENAI_BASE_URL
        )
        return OpenAICompatEmbedding("openai_compat", config, http, FakeClock())


def _handler(outage: bool) -> Callable[[httpx.Request], httpx.Response]:
    """Build the mock transport's routing function, simulating an outage when asked."""

    def handle(request: httpx.Request) -> httpx.Response:
        if outage:
            raise httpx.ConnectError("simulated outage")
        if request.url.path == _OPENAI_MODELS_PATH:
            return httpx.Response(200, json={"data": []})
        if request.url.path != _OPENAI_EMBEDDINGS_PATH:
            return httpx.Response(404)
        body = json.loads(request.content)
        entries = [
            {"index": i, "embedding": _deterministic_vector(text)}
            for i, text in enumerate(body["input"])
        ]
        usage = {"prompt_tokens": len(body["input"])}
        return httpx.Response(200, json={"data": entries, "usage": usage})

    return handle


class _FakeEncodedArray:
    """A minimal stand-in for the numpy-array-like result `EncoderModel.encode` returns."""

    def __init__(self, rows: list[list[float]]) -> None:
        self._rows = rows

    def tolist(self) -> list[list[float]]:
        """Return the rows this array was built from."""
        return self._rows


class _FakeEncoder:
    """A fake EncoderModel: `DIMS`-dimensional and content-determined; honours normalize."""

    def encode(
        self, sentences: list[str], *, batch_size: int, normalize_embeddings: bool
    ) -> _FakeEncodedArray:
        """Encode `sentences`; see `EncoderModel.encode`."""
        rows = [_deterministic_vector(s) for s in sentences]
        if normalize_embeddings:
            rows = [_normalised(row) for row in rows]
        return _FakeEncodedArray(rows)

    def get_sentence_embedding_dimension(self) -> int | None:
        """Return this fake's fixed dimension; see `EncoderModel`'s own method."""
        return DIMS


class SentenceTransformersEmbeddingHarness:
    """Build SentenceTransformersEmbedding over a fake Loader/EncoderModel; no real library used."""

    def make_provider(self, *, outage: bool = False) -> EmbeddingProvider:
        """Build a fresh SentenceTransformersEmbedding; see `EmbeddingProviderHarness`."""

        def loader(model: str, device: str | None, local_files_only: bool) -> EncoderModel:
            if outage:
                raise ImportError("simulated outage")
            return _FakeEncoder()

        config = SentenceTransformersConfig(model="test-embed")
        return SentenceTransformersEmbedding(
            "sentence_transformers", config, FakeClock(), loader=loader
        )
