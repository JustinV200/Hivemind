"""Tests for hivemind.llm.slots: BoundModel.

`resolve(slot, manifest) -> BoundModel` does not exist yet (roadmap step 3.4, a later dispatch);
this module only exercises the BoundModel shape itself.

Fits into the Hive:
    Mirrors src/hivemind/llm/slots.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.slots for the module under test.
"""

from __future__ import annotations

import dataclasses

import pytest

from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.slots import BoundModel


def _make_bound(**overrides: object) -> BoundModel:
    """Build a valid BoundModel, filling in every field with a plain default.

    `**overrides: object` (rather than one named parameter per field) is what keeps this a
    one-parameter function under codingrules 5.1's parameter limit; mypy's `dataclasses.replace`
    special-casing checks each keyword against `BoundModel`'s real field types, which a
    `dict[str, object]` spread can never satisfy statically, so the one broad ignore below covers
    a call every individual test in this module makes correctly at runtime.
    """
    base = BoundModel(
        slot=ModelSlot.WORKER,
        binding="worker",
        provider=FakeLLMProvider(),
        model="test-model",
        effort=Effort.MEDIUM,
        context_window=128_000,
        cost_per_million_input_usd=1.5,
        cost_per_million_output_usd=7.5,
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


def test_bound_model_defaults_fallback_to_none() -> None:
    bound = _make_bound()

    assert bound.fallback is None


def test_bound_model_carries_every_field_it_was_built_with() -> None:
    provider = FakeLLMProvider(name="local")

    bound = _make_bound(
        slot=ModelSlot.QUEEN,
        binding="queen",
        provider=provider,
        model="big-model",
        effort=Effort.HIGH,
        context_window=200_000,
    )

    assert bound.slot is ModelSlot.QUEEN
    assert bound.binding == "queen"
    assert bound.provider is provider
    assert bound.model == "big-model"
    assert bound.effort is Effort.HIGH
    assert bound.context_window == 200_000


def test_bound_model_accepts_none_costs_for_an_unpriced_binding() -> None:
    bound = _make_bound(cost_per_million_input_usd=None, cost_per_million_output_usd=None)

    assert bound.cost_per_million_input_usd is None
    assert bound.cost_per_million_output_usd is None


def test_bound_model_chains_a_fallback() -> None:
    fallback = _make_bound(binding="local_worker")

    primary = _make_bound(fallback=fallback)

    assert primary.fallback is fallback
    assert primary.fallback.fallback is None


def test_bound_model_is_frozen() -> None:
    bound = _make_bound()

    with pytest.raises(dataclasses.FrozenInstanceError):
        bound.model = "other-model"  # type: ignore[misc]  # The assignment is the test.


def test_bound_model_equality_is_by_value() -> None:
    provider = FakeLLMProvider()

    first = _make_bound(provider=provider)
    second = _make_bound(provider=provider)

    assert first == second
