"""Tests for hivemind.llm.embedding.chain: the EMBEDDER slot's chain walk and its binding's price.

Fits into the Hive:
    Mirrors src/hivemind/llm/embedding/chain.py (codingrules section 3). The registry's own
    `embedder()` tests cover these functions in use; these pin each rule on its own.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.embedding.chain for the functions under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_binding

from hivemind.forage.map import ForageMap
from hivemind.forage.models.sources import Abundance, ModelCost, ModelSource, ModelSourceSpec
from hivemind.llm.embedding import embedder_cost, walk_embedder_chain
from hivemind.llm.slots import UnresolvableSlotError
from waggle.clock import FakeClock


def _source(provider: str, model: str, input_price: float) -> ModelSource:
    """One Forage map source serving `model` on `provider` at `input_price` per million tokens."""
    return ModelSource(
        source_id=f"{provider}_{model}",
        spec=ModelSourceSpec(
            provider=provider,
            model=model,
            grade=2,
            context_window=8_192,
            cost=ModelCost(cost_per_million_input_usd=input_price, cost_per_million_output_usd=0.0),
        ),
        abundance=Abundance(seats_free=1),
    )


def test_walk_embedder_chain_follows_fallbacks_in_order() -> None:
    primary = make_binding(key="embedder", provider="local", model="embed", fallback="backup")
    backup = make_binding(key="backup", provider="other", model="embed")
    by_key = {binding.key: binding for binding in (primary, backup)}

    chain = walk_embedder_chain("embedder", by_key)

    assert chain == [primary, backup]


def test_walk_embedder_chain_ends_at_a_fallback_with_no_row() -> None:
    primary = make_binding(key="embedder", provider="local", model="embed", fallback="missing")

    chain = walk_embedder_chain("embedder", {"embedder": primary})

    assert chain == [primary]


def test_walk_embedder_chain_raises_when_the_start_has_no_row() -> None:
    with pytest.raises(UnresolvableSlotError):
        walk_embedder_chain("embedder", {})


def test_walk_embedder_chain_raises_on_a_cycle_instead_of_looping() -> None:
    first = make_binding(key="embedder", provider="local", model="embed", fallback="second")
    second = make_binding(key="second", provider="local", model="embed", fallback="embedder")

    with pytest.raises(UnresolvableSlotError):
        walk_embedder_chain("embedder", {"embedder": first, "second": second})


def test_embedder_cost_reads_the_matching_sources_input_price() -> None:
    binding = make_binding(key="embedder", provider="local", model="embed")
    map_ = ForageMap([_source("other", "embed", 9.0), _source("local", "embed", 0.25)], FakeClock())

    assert embedder_cost(binding, map_) == 0.25


def test_embedder_cost_is_none_without_a_map_or_a_matching_source() -> None:
    binding = make_binding(key="embedder", provider="local", model="embed")
    unrelated = ForageMap([_source("local", "another-model", 1.0)], FakeClock())

    assert embedder_cost(binding, None) is None
    assert embedder_cost(binding, unrelated) is None
