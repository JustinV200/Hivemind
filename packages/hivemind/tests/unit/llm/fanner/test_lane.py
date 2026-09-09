"""Tests for hivemind.llm.fanner.lane: Fanner and FannerLane.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/lane.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.lane for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from builders.forage import make_source
from builders.llm import make_bound, make_request, make_tool, text_response
from pydantic import BaseModel

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelSource
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.capabilities import HealthState, ProviderCapabilities, ProviderHealth
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.fanner.lane import Fanner, FannerDeps, FannerLane
from hivemind.llm.fanner.limiter import RateLimit
from hivemind.llm.fanner.recorder import TrailLlmEventRecorder
from hivemind.llm.ladders.structured import complete_structured
from hivemind.llm.ladders.tools import ToolExecutor, ToolLoopOptions, run_tool_loop
from hivemind.llm.models import LLMChunk, LLMRequest, LLMResponse, ToolCall, ToolResultPart
from hivemind.pheromone import LlmEvent, MemoryPheromoneTrail, TrailQuery
from waggle.clock import Clock, FakeClock
from waggle.ids import new_hive_id, new_node_id


@dataclass(slots=True)
class _BlockingProvider:
    """An LLMProvider whose `complete` awaits an external asyncio.Event before returning.

    FakeLLMProvider's Responder is a plain synchronous callable, which can never actually suspend
    the calling task; this stub lets a test hold a call open until it explicitly `release()`s it,
    which is what proves seat serialisation (FannerLane never starts a second call on a seats=1
    binding before the first one completes).
    """

    calls: list[LLMRequest] = field(default_factory=list)
    _released: asyncio.Event = field(default_factory=asyncio.Event)
    _clock: Clock = field(default_factory=FakeClock)

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities.full()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        await self._released.wait()
        return text_response("blocked-then-ok")

    def stream(self, request: LLMRequest) -> AsyncIterator[LLMChunk]:
        raise NotImplementedError("not used by these tests")

    async def count_tokens(self, request: LLMRequest) -> int | None:
        return None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())

    def release(self) -> None:
        """Unblock every call currently waiting (and every future one) on this provider."""
        self._released.set()


def _tempo(bar: AccuracyBar, latency_budget_s: float | None = None) -> Tempo:
    return Tempo(accuracy=bar, latency_budget_s=latency_budget_s)


def _build_fanner(
    sources: tuple[ModelSource, ...] = (),
    seats: dict[str, int] | None = None,
    limits: dict[str, RateLimit] | None = None,
    clock: Clock | None = None,
) -> tuple[Fanner, MemoryPheromoneTrail]:
    """Wire a Fanner over a fresh ForageMap and a trail-backed recorder, sharing one clock."""
    active_clock = clock if clock is not None else FakeClock()
    trail = MemoryPheromoneTrail(active_clock)
    recorder = TrailLlmEventRecorder(
        trail, new_hive_id(active_clock), new_node_id(active_clock), "human", active_clock
    )
    deps = FannerDeps(
        map=ForageMap(sources, clock=active_clock),
        seats=seats if seats is not None else {},
        limits=limits if limits is not None else {},
        clock=active_clock,
        recorder=recorder,
    )
    return Fanner(deps), trail


async def test_fanner_lane_stamps_the_bound_model_and_returns_the_response() -> None:
    fanner, _ = _build_fanner()
    provider = FakeLLMProvider()
    provider.script(text_response("hi"))
    bound = make_bound(provider=provider, model="local-small")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    response = await lane.complete(bound, make_request())

    assert response.text == "hi"
    assert provider.calls[0].model == "local-small"


async def test_fanner_lane_seats_serialise_the_second_call_until_the_first_completes() -> None:
    fanner, _ = _build_fanner(seats={"fake": 1})
    provider = _BlockingProvider()
    bound = make_bound(provider=provider, binding="worker")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    first = asyncio.ensure_future(lane.complete(bound, make_request()))
    await asyncio.sleep(0)  # Let the first call reach the provider and start blocking.
    assert len(provider.calls) == 1

    second = asyncio.ensure_future(lane.complete(bound, make_request()))
    await asyncio.sleep(0)  # Let the second call reach (and queue behind) the seat meter.
    assert len(provider.calls) == 1  # Still not started: seats=1 blocks it behind the first.
    assert fanner.queued("fake") == 1

    provider.release()
    await first
    await second

    assert len(provider.calls) == 2
    assert fanner.in_flight("fake") == 0


async def test_two_bindings_on_one_provider_share_that_provider_s_single_seat_budget() -> None:
    fanner, _ = _build_fanner(seats={"fake": 1})
    provider = _BlockingProvider()
    # Two manifest rows, one provider: the second row must not buy a second seat.
    first_bound = make_bound(provider=provider, binding="worker")
    second_bound = make_bound(provider=provider, binding="local_worker")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    first = asyncio.ensure_future(lane.complete(first_bound, make_request()))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(lane.complete(second_bound, make_request()))
    await asyncio.sleep(0)

    assert len(provider.calls) == 1  # The other binding queued behind the shared seat.
    assert fanner.queued("fake") == 1

    provider.release()
    await first
    await second
    assert len(provider.calls) == 2


async def test_fanner_lane_admits_a_higher_tempo_lane_before_a_lower_one_already_queued() -> None:
    fanner, _ = _build_fanner(seats={"fake": 1})
    provider = _BlockingProvider()
    bound = make_bound(provider=provider, binding="worker")
    low_lane = fanner.lane(_tempo(AccuracyBar.LOW))
    critical_lane = fanner.lane(_tempo(AccuracyBar.CRITICAL))
    order: list[str] = []

    async def _run(label: str, lane: FannerLane) -> None:
        await lane.complete(bound, make_request())
        order.append(label)

    blocker = asyncio.ensure_future(_run("blocker", low_lane))
    await asyncio.sleep(0)  # Takes the only seat and starts blocking.

    low_task = asyncio.ensure_future(_run("low", low_lane))
    await asyncio.sleep(0)  # Queues behind the blocker.
    critical_task = asyncio.ensure_future(_run("critical", critical_lane))
    await asyncio.sleep(0)  # Queues behind the blocker too, but at a higher priority.

    provider.release()
    await blocker
    await critical_task
    await low_task

    assert order == ["blocker", "critical", "low"]


async def test_fanner_lane_spills_on_grade_below_floor() -> None:
    source = make_source(source_id="src_1", provider="fake", model="test-model", grade=1, seats=1)
    fanner, trail = _build_fanner(sources=(source,))
    primary = FakeLLMProvider(name="fake")
    fallback_provider = FakeLLMProvider(name="local")
    fallback_provider.script(text_response("from-fallback"))
    fallback = make_bound(binding="local_worker", provider=fallback_provider, model="local-small")
    bound = make_bound(binding="worker", provider=primary, model="test-model", fallback=fallback)
    # CRITICAL's grade_floor is 4; the map's only source is grade 1, so it can never clear it.
    lane = fanner.lane(_tempo(AccuracyBar.CRITICAL))

    response = await lane.complete(bound, make_request())

    assert response.text == "from-fallback"
    assert primary.calls == []  # The primary binding was spilled before ever being called.
    # The spill lands one event; the fallback's own successful call lands a second (llm.call).
    events = await trail.query(TrailQuery())
    (spill_event,) = [e for e in events if e.kind == "llm.spill"]
    assert spill_event.payload["reason"] == "GRADE_BELOW_FLOOR"
    assert spill_event.payload["from_binding"] == "worker"
    assert spill_event.payload["to_binding"] == "local_worker"


async def test_fanner_lane_spills_on_model_not_loaded() -> None:
    source = make_source(source_id="src_1", provider="fake", model="test-model", grade=5, seats=0)
    fanner, trail = _build_fanner(sources=(source,))
    primary = FakeLLMProvider(name="fake")
    fallback_provider = FakeLLMProvider(name="local")
    fallback_provider.script(text_response("from-fallback"))
    fallback = make_bound(binding="local_worker", provider=fallback_provider, model="local-small")
    bound = make_bound(binding="worker", provider=primary, model="test-model", fallback=fallback)
    lane = fanner.lane(_tempo(AccuracyBar.LOW))

    response = await lane.complete(bound, make_request())

    assert response.text == "from-fallback"
    events = await trail.query(TrailQuery())
    (spill_event,) = [e for e in events if e.kind == "llm.spill"]
    assert spill_event.payload["reason"] == "MODEL_NOT_LOADED"


async def test_fanner_lane_spills_on_queue_wait_exceeded() -> None:
    clock = FakeClock()
    fanner, trail = _build_fanner(seats={"fake": 1}, clock=clock)
    blocker_provider = _BlockingProvider()
    fallback_provider = FakeLLMProvider(name="local")
    fallback_provider.script(text_response("from-fallback"))
    fallback = make_bound(binding="local_worker", provider=fallback_provider, model="local-small")
    bound = make_bound(
        binding="worker", provider=blocker_provider, model="test-model", fallback=fallback
    )
    # A tight latency budget: any real queueing exceeds SPILL_WAIT_FRACTION of it immediately.
    tempo = _tempo(AccuracyBar.NORMAL, latency_budget_s=0.001)
    lane = fanner.lane(tempo)

    blocker = asyncio.ensure_future(lane.complete(bound, make_request()))
    await asyncio.sleep(0)  # Takes the only seat and starts blocking.

    waiting = asyncio.ensure_future(lane.complete(bound, make_request()))
    await asyncio.sleep(0)  # Let the second call reach and queue behind the seat meter.
    # Advance the fake clock so the queued call's measured wait exceeds the tiny budget.
    clock.advance(1.0)
    blocker_provider.release()

    response = await waiting
    await blocker

    assert response.text == "from-fallback"
    events = await trail.query(TrailQuery())
    assert any(
        e.kind == "llm.spill" and e.payload["reason"] == "QUEUE_WAIT_EXCEEDED" for e in events
    )


async def test_fanner_lane_never_spills_for_an_unknown_source_but_still_meters_it() -> None:
    fanner, trail = _build_fanner()  # No sources on the map at all.
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("ok"))
    fallback_provider = FakeLLMProvider(name="local")
    fallback = make_bound(binding="local_worker", provider=fallback_provider, model="local-small")
    bound = make_bound(binding="worker", provider=provider, model="test-model", fallback=fallback)
    lane = fanner.lane(_tempo(AccuracyBar.CRITICAL))

    response = await lane.complete(bound, make_request())

    assert response.text == "ok"
    assert fallback_provider.calls == []  # Never spilled: an unknown source is never judged.
    events = await trail.query(TrailQuery())
    assert all(e.kind != "llm.spill" for e in events)


async def test_fanner_lane_proceeds_on_the_current_binding_when_the_chain_ends() -> None:
    source = make_source(source_id="src_1", provider="fake", model="test-model", grade=1, seats=0)
    fanner, _ = _build_fanner(sources=(source,))
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("still-answered"))
    bound = make_bound(binding="worker", provider=provider, model="test-model")  # No fallback.
    lane = fanner.lane(_tempo(AccuracyBar.CRITICAL))

    response = await lane.complete(bound, make_request())

    # Both GRADE_BELOW_FLOOR and MODEL_NOT_LOADED apply, but there is nowhere to spill to.
    assert response.text == "still-answered"
    assert len(provider.calls) == 1


async def test_fanner_lane_records_llm_call_with_the_required_fields() -> None:
    fanner, trail = _build_fanner()
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("ok"))
    bound = make_bound(provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    await lane.complete(bound, make_request())

    (event,) = await trail.query(TrailQuery())
    assert isinstance(event, LlmEvent)
    assert event.kind == "llm.call"
    assert event.slot == "WORKER"
    assert event.provider == "fake"
    assert event.usage is not None
    assert "latency_s" in event.payload


async def test_fanner_lane_updates_the_map_on_a_known_sources_success() -> None:
    # ModelSourceSpec.seats (a source's own capacity, judged by MODEL_NOT_LOADED) is a separate
    # figure from FannerDeps.seats (the provider-level meter capacity, v0's stand-in for it, per
    # the module docstring's "Key invariants"); this test sets both to 2 so there is exactly one
    # figure to reason about.
    source = make_source(source_id="src_1", provider="fake", model="test-model", grade=5, seats=2)
    fanner, _ = _build_fanner(sources=(source,), seats={"fake": 2})
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("ok"))
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    await lane.complete(bound, make_request())

    updated = fanner.deps.map.get("src_1")
    assert updated.distance is not None
    # The call's own seat is released (in a `finally`) before this figure is recorded, so all
    # 2 of the binding's meter capacity reads as free again once the call has completed.
    assert updated.abundance.seats_free == 2


async def test_fanner_lane_provider_error_propagates_and_releases_the_seat() -> None:
    fanner, _ = _build_fanner()
    provider = FakeLLMProvider(name="fake")
    provider.script(ProviderUnavailableError("fake", "simulated outage"))
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    try:
        await lane.complete(bound, make_request())
    except ProviderUnavailableError:
        pass
    else:
        raise AssertionError("expected ProviderUnavailableError to propagate")

    assert fanner.in_flight("fake") == 0


async def test_fanner_lane_conforms_to_callgate_via_complete_structured() -> None:
    fanner, _ = _build_fanner()
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response('{"value": 7}'))
    bound = make_bound(provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    class _Schema(BaseModel):
        value: int

    result = await complete_structured(bound, make_request(), _Schema, gate=lane)

    assert result.value.value == 7


async def test_fanner_lane_conforms_to_callgate_via_run_tool_loop() -> None:
    fanner, _ = _build_fanner()
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("done"))
    bound = make_bound(provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    class _NoopExecutor:
        async def execute(self, call: ToolCall) -> ToolResultPart:
            return ToolResultPart(call_id=call.id, content="unused")

    executor: ToolExecutor = _NoopExecutor()
    tools = (make_tool(),)

    result = await run_tool_loop(
        bound, make_request(tools=tools), tools, executor, ToolLoopOptions(gate=lane)
    )

    assert result.final_text == "done"
