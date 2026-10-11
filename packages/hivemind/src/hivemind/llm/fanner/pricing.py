"""Price one completed model call: tokens times per-million rates, plus seat time amortised.

Providers never price a call (their `Usage.cost_usd` is None); this is the one place a price is
applied, so a hosted model, a local one and an amortised local one all reach the Pheromone Trail
(the Hive's append-only audit log) in the same shape. A call costs
`input_tokens x cost_per_million_input_usd / 1e6 + output_tokens x cost_per_million_output_usd /
1e6 + latency_s / 3600 x cost_per_seat_hour_usd`: the last term is how a local source with a
purchase or electricity cost is amortised (the seat, one concurrent request on a model server, was
held for the call's latency).

Fits into the Hive:
    Layer 1 (foundational services), inside `hivemind.llm.fanner`. Called by `FannerLane` after
    each completed call; reads a `BoundModel`'s copied prices and the Forage map source's
    `spec.cost`. Pure: no I/O.

Key invariants:
    - A binding with either per-million price None is unpriced: `cost_usd` is None and `priced`
      is False, never 0.0, so "free" (priced at 0.0) and "unknown" stay distinguishable.
    - A cost the provider itself reported (a fake, or a future adapter whose reply carries a bill)
      is kept as is, counts as priced, and gets no further manifest amortisation.

See Also:
    - hivemind.llm.slots.BoundModel for the copied per-million prices.
    - hivemind.forage.models.sources.ModelCost for `cost_per_seat_hour_usd`.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage.models import ModelSource
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

    # Token component: cached tokens are billed as input here, the conservative reading.
    token_cost = (
        usage.input_tokens * bound.cost_per_million_input_usd
        + usage.output_tokens * bound.cost_per_million_output_usd
    ) / TOKENS_PER_MILLION

    # Seat component: only a known source carries a seat-hour rate; no source means no amortisation.
    seat_rate = source.spec.cost.cost_per_seat_hour_usd if source is not None else 0.0
    seat_cost = max(latency_s, 0.0) / SECONDS_PER_HOUR * seat_rate

    return PricedUsage(
        usage=usage.model_copy(update={"cost_usd": token_cost + seat_cost}), priced=True
    )
