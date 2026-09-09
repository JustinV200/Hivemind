"""Tests for hivemind.llm.slots: BoundModel, resolve and resolve_key.

Fits into the Hive:
    Mirrors src/hivemind/llm/slots.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.slots for the module under test.
    - docs/manifests/ for the example manifests the resolve() tests load.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from builders.llm import bindings_from_manifest, make_binding

from hivemind.forage.map import ForageMap
from hivemind.forage.models.sources import Abundance, ModelCost, ModelSource, ModelSourceSpec
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.llm.errors import UnknownProviderError
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.provider import LLMProvider
from hivemind.llm.slots import BoundModel, UnresolvableSlotError, resolve, resolve_key
from hivemind.manifest import load_manifest
from waggle.clock import FakeClock

# Same depth as tests/unit/manifest/test_loader.py's own _REPO_ROOT: five parents up from
# packages/hivemind/tests/unit/llm/test_slots.py.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_MANIFESTS_DIR = _REPO_ROOT / "docs" / "manifests"


def _lookup(name: str) -> LLMProvider:
    """A ProviderLookup that hands back a fresh FakeLLMProvider named `name`, every time."""
    return FakeLLMProvider(name=name)


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


# ──────────────────────────────────────────────────────────────────────────────
# resolve() against the shipped example manifests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slot", list(ModelSlot))
def test_resolve_binds_every_slot_in_minimal_manifest(slot: ModelSlot) -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "minimal.toml")
    bindings = bindings_from_manifest(manifest)

    bound = resolve(slot, bindings.values(), _lookup)

    assert bound.slot is slot
    assert bound.binding == slot.manifest_key
    assert bound.provider.name == "anthropic"
    assert bound.model == bindings[slot.manifest_key].model
    assert bound.fallback is None


@pytest.mark.parametrize("slot", list(ModelSlot))
def test_resolve_binds_every_slot_in_local_manifest_to_the_local_provider(
    slot: ModelSlot,
) -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "local.toml")
    bindings = bindings_from_manifest(manifest)

    bound = resolve(slot, bindings.values(), _lookup)

    assert bound.provider.name == "local"
    assert bound.model == bindings[slot.manifest_key].model
    assert bound.fallback is None


def test_resolve_follows_a_named_binding_fallback_in_the_full_manifest() -> None:
    manifest = load_manifest(_MANIFESTS_DIR / "full.toml")
    bindings = bindings_from_manifest(manifest)

    bound = resolve(ModelSlot.WORKER, bindings.values(), _lookup)

    assert bound.binding == "worker"
    assert bound.provider.name == "anthropic"
    assert bound.fallback is not None
    # The fallback still serves ModelSlot.WORKER: only the binding link changed.
    assert bound.fallback.slot is ModelSlot.WORKER
    assert bound.fallback.binding == "local_worker"
    assert bound.fallback.provider.name == "local"
    assert bound.fallback.fallback is None


def test_resolve_uses_the_providers_declared_context_window() -> None:
    bound = resolve(ModelSlot.WORKER, [make_binding()], _lookup)

    # FakeLLMProvider defaults to ProviderCapabilities.full(), whose context_window is 200_000.
    assert bound.context_window == 200_000


# ──────────────────────────────────────────────────────────────────────────────
# Pricing from a Forage map
# ──────────────────────────────────────────────────────────────────────────────


def test_resolve_prices_a_binding_from_its_matching_map_source() -> None:
    binding = make_binding(provider="anthropic", model="test-model")
    source = ModelSource(
        source_id="anthropic_test",
        spec=ModelSourceSpec(
            provider="anthropic",
            model="test-model",
            grade=4,
            context_window=100_000,
            cost=ModelCost(cost_per_million_input_usd=2.0, cost_per_million_output_usd=10.0),
        ),
        abundance=Abundance(seats_free=1),
    )
    map_ = ForageMap([source], clock=FakeClock())

    bound = resolve(ModelSlot.WORKER, [binding], _lookup, map_)

    assert bound.cost_per_million_input_usd == 2.0
    assert bound.cost_per_million_output_usd == 10.0


def test_resolve_leaves_cost_none_without_a_map() -> None:
    bound = resolve(ModelSlot.WORKER, [make_binding()], _lookup)

    assert bound.cost_per_million_input_usd is None
    assert bound.cost_per_million_output_usd is None


def test_resolve_leaves_cost_none_when_no_map_source_matches() -> None:
    binding = make_binding(provider="anthropic", model="test-model")
    other_source = ModelSource(
        source_id="unrelated",
        spec=ModelSourceSpec(provider="other", model="other-model", grade=1, context_window=8_192),
        abundance=Abundance(seats_free=1),
    )
    map_ = ForageMap([other_source], clock=FakeClock())

    bound = resolve(ModelSlot.WORKER, [binding], _lookup, map_)

    assert bound.cost_per_million_input_usd is None
    assert bound.cost_per_million_output_usd is None


# ──────────────────────────────────────────────────────────────────────────────
# Guards: a missing row, a cycle, and provider lookup failure
# ──────────────────────────────────────────────────────────────────────────────


def test_resolve_raises_when_bindings_has_no_row_for_the_slot() -> None:
    with pytest.raises(UnresolvableSlotError, match="no such row"):
        resolve(ModelSlot.WORKER, [], _lookup)


def test_resolve_raises_on_a_synthetic_cycle() -> None:
    # Hand-built to bypass the manifest's own cycle-rejecting validator (hivemind.llm.slots does
    # not import hivemind.manifest at all -- see that module's docstring) and prove resolve()'s
    # own bounded-walk guard instead.
    first = make_binding(key="worker", fallback="local_worker")
    second = make_binding(key="local_worker", fallback="worker")

    with pytest.raises(UnresolvableSlotError, match="cycles back"):
        resolve(ModelSlot.WORKER, [first, second], _lookup)


def test_resolve_propagates_the_provider_lookups_own_error() -> None:
    binding = make_binding(provider="missing")

    def _lookup_none(name: str) -> LLMProvider:
        raise UnknownProviderError(name)

    with pytest.raises(UnknownProviderError):
        resolve(ModelSlot.WORKER, [binding], _lookup_none)


# ──────────────────────────────────────────────────────────────────────────────
# resolve_key(): a rebind to a named binding
# ──────────────────────────────────────────────────────────────────────────────


def test_resolve_key_binds_a_named_binding_as_the_given_slot() -> None:
    named = make_binding(key="local_worker", provider="local", model="local-small")

    bound = resolve_key("local_worker", ModelSlot.WORKER, [named], _lookup)

    assert bound.slot is ModelSlot.WORKER
    assert bound.binding == "local_worker"
    assert bound.provider.name == "local"
    assert bound.model == "local-small"


def test_resolve_key_raises_when_bindings_has_no_row_for_the_key() -> None:
    with pytest.raises(UnresolvableSlotError, match="no such row"):
        resolve_key("nowhere", ModelSlot.WORKER, [], _lookup)
