"""Tests for hivemind.llm.fanner.pricing: one call's cost from tokens and seat time.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/pricing.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.pricing for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_source
from builders.llm import make_bound

from hivemind.forage.models import ModelCost
from hivemind.llm.fanner.pricing import price_usage
from hivemind.llm.models import Usage

_USAGE = Usage(input_tokens=2_000_000, output_tokens=1_000_000)


def test_price_usage_applies_per_million_rates_to_both_directions() -> None:
    bound = make_bound(cost_per_million_input_usd=1.5, cost_per_million_output_usd=7.5)

    priced = price_usage(bound, None, _USAGE, latency_s=2.0)

    assert priced.priced is True
    assert priced.usage.cost_usd == pytest.approx(2 * 1.5 + 7.5)


def test_price_usage_amortises_a_local_source_by_latency_in_seat_hours() -> None:
    bound = make_bound(cost_per_million_input_usd=0.0, cost_per_million_output_usd=0.0)
    source = make_source(cost=ModelCost(cost_per_seat_hour_usd=3.6))

    priced = price_usage(bound, source, Usage(input_tokens=10, output_tokens=10), latency_s=1800.0)

    assert priced.usage.cost_usd == pytest.approx(1.8)  # Half an hour of a 3.6 dollar seat.
    assert priced.priced is True


def test_price_usage_adds_token_and_seat_components() -> None:
    bound = make_bound(cost_per_million_input_usd=1.0, cost_per_million_output_usd=2.0)
    source = make_source(cost=ModelCost(cost_per_seat_hour_usd=7200.0))

    priced = price_usage(bound, source, _USAGE, latency_s=1.0)

    assert priced.usage.cost_usd == pytest.approx(2.0 + 2.0 + 2.0)


def test_price_usage_prices_a_free_source_at_exactly_zero() -> None:
    bound = make_bound(cost_per_million_input_usd=0.0, cost_per_million_output_usd=0.0)
    source = make_source(cost=ModelCost())

    priced = price_usage(bound, source, _USAGE, latency_s=30.0)

    assert priced.usage.cost_usd == 0.0
    assert priced.priced is True


def test_price_usage_leaves_an_unpriced_binding_unpriced_not_free() -> None:
    bound = make_bound(cost_per_million_input_usd=None, cost_per_million_output_usd=None)
    source = make_source(cost=ModelCost(cost_per_seat_hour_usd=3.6))

    priced = price_usage(bound, source, _USAGE, latency_s=30.0)

    assert priced.usage.cost_usd is None
    assert priced.priced is False


@pytest.mark.parametrize("prices", [(None, None), (1.0, 2.0)])
def test_price_usage_keeps_a_cost_the_provider_reported(
    prices: tuple[float | None, float | None],
) -> None:
    bound = make_bound(cost_per_million_input_usd=prices[0], cost_per_million_output_usd=prices[1])
    reported = Usage(input_tokens=1, output_tokens=1, cost_usd=0.25)

    priced = price_usage(bound, None, reported, latency_s=1.0)

    assert priced.usage.cost_usd == 0.25
    assert priced.priced is True


def test_price_usage_keeps_the_estimated_flag() -> None:
    bound = make_bound(cost_per_million_input_usd=1.0, cost_per_million_output_usd=1.0)
    guessed = Usage(input_tokens=4, output_tokens=4, estimated=True)

    priced = price_usage(bound, None, guessed, latency_s=0.0)

    assert priced.usage.estimated is True


def test_price_usage_bills_cache_reads_and_writes_at_the_rows_ratios() -> None:
    # An Anthropic-shaped usage: 500 fresh input tokens, 8,000 read from the cache, 1,000 written
    # to it (the adapter has already summed them into `input_tokens`), and 100 output tokens.
    usage = Usage(
        input_tokens=9_500, output_tokens=100, cached_tokens=8_000, cache_write_tokens=1_000
    )
    bound = make_bound(cost_per_million_input_usd=3.0, cost_per_million_output_usd=15.0)
    source = make_source(cost=ModelCost(cost_per_million_input_usd=3.0))

    priced = price_usage(bound, source, usage, latency_s=0.0)

    # Fresh at the full rate, reads at the default tenth, writes at the default quarter over.
    expected = (500 * 3.0 + 8_000 * 3.0 * 0.1 + 1_000 * 3.0 * 1.25 + 100 * 15.0) / 1_000_000
    assert priced.usage.cost_usd == pytest.approx(expected)
    assert priced.priced is True


def test_price_usage_reads_the_cache_ratios_from_the_sources_row() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=0, cached_tokens=1_000_000)
    bound = make_bound(cost_per_million_input_usd=2.0, cost_per_million_output_usd=0.0)
    # A provider that halves a cache read rather than discounting it to a tenth.
    source = make_source(cost=ModelCost(cost_per_million_input_usd=2.0, cache_read_price_ratio=0.5))

    priced = price_usage(bound, source, usage, latency_s=0.0)

    assert priced.usage.cost_usd == pytest.approx(1.0)


def test_price_usage_uses_the_documented_cache_defaults_for_an_unknown_source() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=0, cached_tokens=1_000_000)
    bound = make_bound(cost_per_million_input_usd=10.0, cost_per_million_output_usd=0.0)

    priced = price_usage(bound, None, usage, latency_s=0.0)

    assert priced.usage.cost_usd == pytest.approx(1.0)  # A tenth of the full input rate.
