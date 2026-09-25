"""Tests for hivemind.llm.embedding.capabilities: EmbeddingCapabilities.

Fits into the Hive:
    Mirrors src/hivemind/llm/embedding/capabilities.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.embedding.capabilities for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.llm.embedding.capabilities import EmbeddingCapabilities


def test_dimensions_defaults_to_none() -> None:
    capabilities = EmbeddingCapabilities(max_batch=32, max_input_chars=8_000, normalized=True)

    assert capabilities.dimensions is None


def test_every_field_round_trips() -> None:
    capabilities = EmbeddingCapabilities(
        dimensions=768, max_batch=64, max_input_chars=4_000, normalized=False
    )

    restored = EmbeddingCapabilities.model_validate_json(capabilities.model_dump_json())

    assert restored == capabilities


def test_rejects_a_non_positive_max_batch() -> None:
    with pytest.raises(ValidationError):
        EmbeddingCapabilities(max_batch=0, max_input_chars=1, normalized=True)


def test_rejects_a_non_positive_max_input_chars() -> None:
    with pytest.raises(ValidationError):
        EmbeddingCapabilities(max_batch=1, max_input_chars=0, normalized=True)


def test_is_frozen_and_forbids_extras() -> None:
    capabilities = EmbeddingCapabilities(max_batch=1, max_input_chars=1, normalized=True)

    with pytest.raises(ValidationError, match="frozen"):
        capabilities.normalized = False  # type: ignore[misc]  # The assignment is the test.
    with pytest.raises(ValidationError, match="extra"):
        EmbeddingCapabilities.model_validate({**capabilities.model_dump(), "bogus": 1})
