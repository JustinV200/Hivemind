"""Tests for hivemind.llm.embedding.models: EmbeddingRequest and EmbeddingResponse.

Fits into the Hive:
    Mirrors src/hivemind/llm/embedding/models.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.embedding.models for the module under test.
"""

from __future__ import annotations

import math

import pytest
from builders.llm import make_embed_request, make_embed_response
from pydantic import ValidationError

from hivemind.llm.embedding.models import MAX_EMBED_TEXTS, EmbeddingRequest, EmbeddingResponse
from hivemind.llm.models import Usage

# ──────────────────────────────────────────────────────────────────────────────
# EmbeddingRequest
# ──────────────────────────────────────────────────────────────────────────────


def test_embedding_request_accepts_one_text() -> None:
    request = make_embed_request(texts=("hello",))

    assert request.texts == ("hello",)


def test_embedding_request_accepts_up_to_max_embed_texts() -> None:
    texts = tuple(f"text-{i}" for i in range(MAX_EMBED_TEXTS))

    request = EmbeddingRequest(texts=texts)

    assert len(request.texts) == MAX_EMBED_TEXTS


def test_embedding_request_rejects_more_than_max_embed_texts() -> None:
    texts = tuple(f"text-{i}" for i in range(MAX_EMBED_TEXTS + 1))

    with pytest.raises(ValidationError):
        EmbeddingRequest(texts=texts)


def test_embedding_request_rejects_zero_texts() -> None:
    with pytest.raises(ValidationError):
        EmbeddingRequest(texts=())


def test_embedding_request_rejects_an_empty_text() -> None:
    with pytest.raises(ValidationError):
        EmbeddingRequest(texts=("",))


def test_embedding_request_is_frozen_and_forbids_extras() -> None:
    request = make_embed_request()

    with pytest.raises(ValidationError, match="frozen"):
        request.texts = ()  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        EmbeddingRequest.model_validate({**request.model_dump(), "nope": 1})


def test_embedding_request_round_trips_through_json() -> None:
    request = make_embed_request(texts=("a", "b"))

    restored = EmbeddingRequest.model_validate_json(request.model_dump_json())

    assert restored == request


# ──────────────────────────────────────────────────────────────────────────────
# EmbeddingResponse
# ──────────────────────────────────────────────────────────────────────────────


def test_embedding_response_accepts_matching_vectors() -> None:
    response = make_embed_response(vectors=((1.0, 0.0), (0.0, 1.0)), dimensions=2)

    assert response.dimensions == 2
    assert len(response.vectors) == 2


def test_embedding_response_rejects_a_vector_of_the_wrong_length() -> None:
    with pytest.raises(ValidationError, match="expected dimensions"):
        make_embed_response(vectors=((1.0, 0.0, 0.0),), dimensions=2)


def test_embedding_response_rejects_a_non_finite_component() -> None:
    with pytest.raises(ValidationError, match="non-finite"):
        make_embed_response(vectors=((math.inf, 0.0, 0.0, 0.0),))


def test_embedding_response_rejects_a_nan_component() -> None:
    with pytest.raises(ValidationError, match="non-finite"):
        make_embed_response(vectors=((math.nan, 0.0, 0.0, 0.0),))


def test_embedding_response_rejects_a_non_positive_dimensions() -> None:
    with pytest.raises(ValidationError):
        make_embed_response(vectors=(), dimensions=0)


def test_embedding_response_is_frozen_and_forbids_extras() -> None:
    response = make_embed_response()

    with pytest.raises(ValidationError, match="frozen"):
        response.model = "other"  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        EmbeddingResponse.model_validate({**response.model_dump(), "nope": 1})


def test_embedding_response_round_trips_through_json() -> None:
    response = make_embed_response(usage=Usage(input_tokens=5, output_tokens=0, cost_usd=0.001))

    restored = EmbeddingResponse.model_validate_json(response.model_dump_json())

    assert restored == response
