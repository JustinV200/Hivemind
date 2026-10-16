"""Price one completed model call: tokens times per-million rates, plus seat time amortised.

Providers never price a call (their `Usage.cost_usd` is None); this is the one place a price is
applied, so a hosted model, a local one and an amortised local one all reach the Pheromone Trail
(the Hive's append-only audit log) in the same shape. A call costs
`fresh_input x cost_per_million_input_usd / 1e6 + cached x that rate x cache_read_price_ratio +
cache_written x that rate x cache_write_price_ratio + output_tokens x
cost_per_million_output_usd / 1e6 + latency_s / 3600 x cost_per_seat_hour_usd`, where
`fresh_input` is `input_tokens` less the cached and cache-written parts (`Usage` carries both as
subsets of `input_tokens`): a hosted provider's prompt cache discounts a read and surcharges a
write, so billing every input token at the plain rate under-counted exactly the stable-prefix
Queen prompts the cache exists for. The last term is how a local source with a purchase or
electricity cost is amortised (the seat, one concurrent request on a model server, was held for
the call's latency).

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.fanner`. Called by `FannerLane` after
    each completed call; reads a `BoundModel`'s copied prices and the Forage map source's
    `spec.cost`. Pure: no I/O.

Key invariants:
    - A binding with either per-million price None is unpriced: `cost_usd` is None and `priced`
      is False, never 0.0, so "free" (priced at 0.0) and "unknown" stay distinguishable.
    - A cost the provider itself reported (a fake, or a future adapter whose reply carries a bill)
      is kept as is, counts as priced, and gets no further manifest amortisation.
    - The cache ratios come from the Forage map source's `ModelCost` row; a binding the map does
      not know is priced with `ModelCost`'s documented defaults, never with a vendor's rate
      written here.

See Also:
    - hivemind.llm.slots.BoundModel for the copied per-million prices.
    - hivemind.forage.models.sources.ModelCost for `cost_per_seat_hour_usd` and the cache ratios.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage.models import ModelCost, ModelSource
from hivemind.llm.models import LLMResponse, Usage
from hivemind.llm.slots import BoundModel

TOKENS_PER_MILLION = 1_000_000  # The unit manifest token prices are quoted in.
SECONDS_PER_HOUR = 3600.0  # Converts a call's latency into the hours a per-seat-hour rate bills.

__all__ = ["PricedUsage", "price_response", "price_usage"]


@dataclass(frozen=True, slots=True)
class PricedUsage:
    """A call's `Usage` with its cost applied, and whether any price backed that cost."""

    usage: Usage  # The call's usage; `cost_usd` is filled in when the call was priced.
    priced: bool  # True when `usage.cost_usd` is a real figure (possibly 0.0 for a free source).


def price_response(
    bound: BoundModel, source: ModelSource | None, response: LLMResponse, latency_s: float
) -> tuple[LLMResponse, PricedUsage]:
    """Return `response` carrying its priced `Usage`, with the `PricedUsage` for the trail.

    Args:
        bound: The binding that served the call.
        source: The Forage map entry that served it, or None if the map does not know it.
        response: What the provider returned; frozen, so the result is a copy.
        latency_s: The call's measured wall time, in seconds.
    """
    priced = price_usage(bound, source, response.usage, latency_s)
    return response.model_copy(update={"usage": priced.usage}), priced


def price_usage(
    bound: BoundModel, source: ModelSource | None, usage: Usage, latency_s: float
) -> PricedUsage:
    """Apply `bound`'s prices and `source`'s seat-hour rate to one call's usage.

    Args:
        bound: The binding that served the call; its two per-million prices are the token rates.
        source: The Forage map entry that served it, or None when the map has never heard of this
            (provider, model); its `spec.cost.cost_per_seat_hour_usd` amortises the latency.
        usage: What the provider reported (or the adapter estimated).
        latency_s: The call's measured wall time, in seconds; non-negative.

    Returns:
        The usage with `cost_usd` set and `priced=True`, or the usage unchanged (provider cost
        kept if it had one) with `priced` reflecting whether that cost exists.
    """
    # A cost the provider itself reported is its own bill: never overwritten by the manifest.
    if usage.cost_usd is not None:
        return PricedUsage(usage=usage, priced=True)

    # Unpriced binding: no price to apply, so the cost stays unknown (None), not zero.
    if bound.cost_per_million_input_usd is None or bound.cost_per_million_output_usd is None:
        return PricedUsage(usage=usage, priced=False)

    # The row's rates: the map's when the source is known, else ModelCost's documented defaults
    # (an unknown source has no seat-hour rate either, so nothing is amortised for it).
    cost = source.spec.cost if source is not None else ModelCost()
    token_cost = (
        _input_cost(usage, bound.cost_per_million_input_usd, cost)
        + usage.output_tokens * bound.cost_per_million_output_usd
    ) / TOKENS_PER_MILLION

    # Seat component: a local source with a purchase or electricity cost, held for the latency.
    seat_cost = max(latency_s, 0.0) / SECONDS_PER_HOUR * cost.cost_per_seat_hour_usd

    return PricedUsage(
        usage=usage.model_copy(update={"cost_usd": token_cost + seat_cost}), priced=True
    )


def _input_cost(usage: Usage, rate_per_million: float, cost: ModelCost) -> float:
    """Return the input side's cost in per-million units: fresh, cache-read and cache-written parts.

    Args:
        usage: The call's usage; `cached_tokens` and `cache_write_tokens` are subsets of
            `input_tokens`, so the fresh part is what remains once both are taken out.
        rate_per_million: The binding's `cost_per_million_input_usd`.
        cost: The row's `ModelCost`, for the two cache ratios.
    """
    # Clamp at zero so an adapter that ever reported overlapping subsets cannot bill negatively.
    fresh = max(usage.input_tokens - usage.cached_tokens - usage.cache_write_tokens, 0)
    return rate_per_million * (
        fresh
        + usage.cached_tokens * cost.cache_read_price_ratio
        + usage.cache_write_tokens * cost.cache_write_price_ratio
    )
