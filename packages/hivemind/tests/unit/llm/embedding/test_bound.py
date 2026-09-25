"""Tests for hivemind.llm.embedding.bound: BoundEmbedder.

Fits into the Hive:
    Mirrors src/hivemind/llm/embedding/bound.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.embedding.bound for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest
from builders.llm import make_bound_embedder

from hivemind.forage.slots import ModelSlot


def test_bound_embedder_defaults_to_the_embedder_slot_and_no_fallback() -> None:
    bound = make_bound_embedder()

    assert bound.slot is ModelSlot.EMBEDDER
    assert bound.fallback is None


def test_bound_embedder_carries_a_fallback_chain() -> None:
    fallback = make_bound_embedder(binding="local_embedder")

    bound = make_bound_embedder(fallback=fallback)

    assert bound.fallback is fallback
    assert bound.fallback.binding == "local_embedder"


def test_bound_embedder_is_frozen() -> None:
    bound = make_bound_embedder()

    # A frozen dataclass raises FrozenInstanceError, not ValidationError (this is a dataclass,
    # not a pydantic model -- codingrules 8.5's "internal values" rule).
    with pytest.raises(dataclasses.FrozenInstanceError):
        bound.model = "other"  # type: ignore[misc]  # The assignment is the test.
