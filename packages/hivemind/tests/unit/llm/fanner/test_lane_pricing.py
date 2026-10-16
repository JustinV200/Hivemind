"""Tests for how FannerLane prices a call and records it on the trail (roadmap step 8.8).

A call's cost, `priced` flag and `estimated` flag all reach the `llm.call` event the same way for a
hosted model, a free local one and an amortised local one; these tests drive a real lane over a
provider that spends fake-clock time so latency is exact.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/lane.py's pricing path (codingrules section 3 allows a
    module's tests to split by feature).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.lane and hivemind.llm.fanner.pricing for the code under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import pytest
from builders.forage import make_source
from builders.llm import make_bound, make_request, make_response

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelCost
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.capabilities import HealthState, ProviderCapabilities, ProviderHealth
from hivemind.llm.fanner.lane import Fanner, FannerDeps
from hivemind.llm.fanner.recorder import TrailLlmEventRecorder
from hivemind.llm.models import LLMChunk, LLMRequest, LLMResponse, Usage
from hivemind.pheromone import LlmEvent, LlmUsage, MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id

_PROVIDER = "local"
_MODEL = "local-small"


@dataclass(slots=True)
class _TimedProvider:
    """A provider that spends `seconds` of fake-clock time per call and returns `usage`."""

    clock: FakeClock
    seconds: float
    usage: Usage

    @property
    def name(self) -> str:
        return _PROVIDER

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities.full()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.clock.advance(self.seconds)  # The call "takes" this long, exactly.
        return make_response(usage=self.usage)

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        raise NotImplementedError("not used by these tests")

    async def count_tokens(self, request: LLMRequest) -> int | None:
        return None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self.clock.now())


async def _run_call(
    *,
    cost: ModelCost,
    prices: tuple[float | None, float | None],
    usage: Usage,
    seconds: float = 1.0,
    locality: Literal["hosted", "provider", "cell"] = "hosted",
) -> tuple[LlmUsage, LLMResponse]:
    """Run one call over a one-source map and return the recorded event plus the response.

    `locality` places the source: "provider" lists it in the Fanner's `local_providers`, "cell"
    puts the map source on a Cell (`host_cell_id`), "hosted" does neither.
    """
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    recorder = TrailLlmEventRecorder(trail, new_hive_id(clock), new_node_id(clock), "human", clock)
    host_cell_id = new_cell_id(clock) if locality == "cell" else None
    source = make_source(
        source_id="src_1",
        provider=_PROVIDER,
        model=_MODEL,
        grade=5,
        cost=cost,
        host_cell_id=host_cell_id,
    )
    deps = FannerDeps(
        map=ForageMap((source,), clock=clock),
        seats={_PROVIDER: 1},
        limits={},
        clock=clock,
        recorder=recorder,
        local_providers=frozenset({_PROVIDER}) if locality == "provider" else frozenset(),
    )
    provider = _TimedProvider(clock, seconds, usage)
    bound = make_bound(
        provider=provider,
        model=_MODEL,
        cost_per_million_input_usd=prices[0],
        cost_per_million_output_usd=prices[1],
    )
    lane = Fanner(deps).lane(Tempo(accuracy=AccuracyBar.NORMAL, latency_budget_s=None))

    response = await lane.complete(bound, make_request())

    (event,) = await trail.query(TrailQuery())
    assert isinstance(event, LlmEvent)
    assert event.usage is not None
    return event.usage, response


async def test_lane_prices_a_local_seat_hour_source_by_latency() -> None:
    usage, response = await _run_call(
        cost=ModelCost(cost_per_seat_hour_usd=3.6),
        prices=(0.0, 0.0),
        usage=Usage(input_tokens=100, output_tokens=50),
        seconds=1800.0,
    )

    assert usage.cost_usd == pytest.approx(1.8)
    assert usage.priced is True
    assert response.usage.cost_usd == pytest.approx(1.8)  # The caller sees the same price.


async def test_lane_records_a_free_local_source_as_priced_zero() -> None:
    usage, response = await _run_call(
        cost=ModelCost(),
        prices=(0.0, 0.0),
        usage=Usage(input_tokens=100, output_tokens=50),
        seconds=5.0,
    )

    assert usage.cost_usd == 0.0
    assert usage.priced is True
    assert response.usage.cost_usd == 0.0


async def test_lane_records_an_unpriced_binding_as_not_priced() -> None:
    usage, response = await _run_call(
        cost=ModelCost(),
        prices=(None, None),
        usage=Usage(input_tokens=100, output_tokens=50),
    )

    assert usage.cost_usd == 0.0  # The trail's convention for "no price" ...
    assert usage.priced is False  # ... which `priced` now tells apart from free.
    assert response.usage.cost_usd is None


async def test_lane_prices_hosted_tokens_per_million() -> None:
    usage, _ = await _run_call(
        cost=ModelCost(),
        prices=(3.0, 15.0),
        usage=Usage(input_tokens=1_000_000, output_tokens=2_000_000),
    )

    assert usage.cost_usd == pytest.approx(33.0)
    assert usage.priced is True


async def test_lane_carries_the_estimated_flag_onto_the_trail() -> None:
    usage, _ = await _run_call(
        cost=ModelCost(),
        prices=(0.0, 0.0),
        usage=Usage(input_tokens=25, output_tokens=5, estimated=True),
    )

    assert usage.estimated is True
    assert usage.priced is True


async def test_lane_records_a_call_on_a_local_provider_as_local() -> None:
    usage, _ = await _run_call(
        cost=ModelCost(cost_per_seat_hour_usd=3.6),
        prices=(0.0, 0.0),
        usage=Usage(input_tokens=100, output_tokens=50),
        locality="provider",
    )

    assert usage.local is True
    assert usage.cost_usd > 0  # Amortised seat time is a cost, yet not hosted spend.


async def test_lane_records_a_call_on_a_source_hosted_by_a_cell_as_local() -> None:
    usage, _ = await _run_call(
        cost=ModelCost(),
        prices=(0.0, 0.0),
        usage=Usage(input_tokens=100, output_tokens=50),
        locality="cell",
    )

    assert usage.local is True


async def test_lane_records_a_call_on_a_hosted_provider_as_not_local() -> None:
    usage, _ = await _run_call(
        cost=ModelCost(),
        prices=(3.0, 15.0),
        usage=Usage(input_tokens=100, output_tokens=50),
    )

    assert usage.local is False
