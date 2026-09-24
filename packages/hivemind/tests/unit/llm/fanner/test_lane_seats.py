"""Tests for hivemind.llm.fanner.lane: seats-in-flight and grant/goal attribution (roadmap 4.8).

Split out of test_lane.py once that file passed codingrules 5.1's 400-line test-file limit
(section 14.2: "split by feature under test before that").

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/lane.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.lane for the module under test.
    - tests/unit/llm/fanner/test_lane.py for the rest of FannerLane's own test suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from builders.forage import make_source
from builders.llm import make_bound, make_request, text_response

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelSource
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.capabilities import HealthState, ProviderCapabilities, ProviderHealth
from hivemind.llm.errors import ProviderUnavailableError
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.fanner.lane import Fanner, FannerDeps
from hivemind.llm.fanner.recorder import LlmEventRecorder, TrailLlmEventRecorder
from hivemind.llm.models import JsonObject, LLMChunk, LLMRequest, LLMResponse
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from waggle.clock import Clock, FakeClock
from waggle.ids import new_hive_id, new_node_id


@dataclass(slots=True)
class _BlockingProvider:
    """An LLMProvider whose `complete` awaits an external asyncio.Event before returning.

    Mirrors test_lane.py's own fixture of the same name and purpose: lets a test hold a call
    open until it explicitly `release()`s it, so a burst of concurrent calls actually overlaps.
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


@dataclass(slots=True)
class _StuckStartRecorder:
    """An LlmEventRecorder whose `call_started` never returns: a slow ledger write, say."""

    finished: list[str] = field(default_factory=list)
    entered: asyncio.Event = field(default_factory=asyncio.Event)

    async def record(self, kind: str, subject_id: str, payload: JsonObject) -> None:
        return None

    async def call_started(self, source_id: str | None, provider: str) -> None:
        self.entered.set()
        await asyncio.Event().wait()  # Held until the calling sub-bee is killed.

    async def call_finished(self, source_id: str | None, provider: str) -> None:
        self.finished.append(provider)


@dataclass(slots=True)
class _SpyRecorder:
    """An LlmEventRecorder that records every call it receives, for asserting call order/args."""

    calls: list[tuple[str, ...]] = field(default_factory=list)
    records: list[tuple[str, str, JsonObject]] = field(default_factory=list)

    async def record(self, kind: str, subject_id: str, payload: JsonObject) -> None:
        self.records.append((kind, subject_id, payload))

    async def call_started(self, source_id: str | None, provider: str) -> None:
        self.calls.append(("started", source_id or "", provider))

    async def call_finished(self, source_id: str | None, provider: str) -> None:
        self.calls.append(("finished", source_id or "", provider))


def _tempo(bar: AccuracyBar) -> Tempo:
    return Tempo(accuracy=bar)


def _build_fanner(
    sources: tuple[ModelSource, ...] = (),
    seats: dict[str, int] | None = None,
    recorder: LlmEventRecorder | None = None,
) -> tuple[Fanner, MemoryPheromoneTrail]:
    """Wire a Fanner over a fresh ForageMap, optionally over a `_SpyRecorder`, one shared clock."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    active_recorder = recorder or TrailLlmEventRecorder(
        trail, new_hive_id(clock), new_node_id(clock), "human", clock
    )
    deps = FannerDeps(
        map=ForageMap(sources, clock=clock),
        seats=seats if seats is not None else {},
        limits={},
        clock=clock,
        recorder=active_recorder,
    )
    return Fanner(deps), trail


async def test_fanner_lane_calls_call_started_then_call_finished_around_the_provider_call() -> None:
    source = make_source(source_id="src_1", provider="fake", model="test-model")
    spy = _SpyRecorder()
    fanner, _ = _build_fanner(sources=(source,), recorder=spy)
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("ok"))
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    await lane.complete(bound, make_request())

    assert spy.calls == [("started", "src_1", "fake"), ("finished", "src_1", "fake")]


async def test_fanner_lane_call_finished_still_fires_when_the_provider_raises() -> None:
    spy = _SpyRecorder()
    fanner, _ = _build_fanner(recorder=spy)  # No source on the map: source_id is None.
    provider = FakeLLMProvider(name="fake")
    provider.script(ProviderUnavailableError("fake", "simulated outage"))
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    with pytest.raises(ProviderUnavailableError):
        await lane.complete(bound, make_request())

    assert spy.calls == [("started", "", "fake"), ("finished", "", "fake")]


async def test_six_calls_against_a_two_seat_source_never_show_more_than_two_in_flight() -> None:
    # Roadmap 4.8's own exit-criteria proof, at the Fanner/recorder boundary: with FannerDeps.
    # seats capped at 2, a burst of six calls must never let call_started outrun call_finished by
    # more than 2 (SeatMeter already enforces the concurrency; this proves the recorder hook
    # reports exactly what SeatMeter enforced, matching ForageLedger's own live "seats in use").
    source = make_source(source_id="src_1", provider="fake", model="test-model", seats=6)
    spy = _SpyRecorder()
    fanner, _ = _build_fanner(sources=(source,), seats={"fake": 2}, recorder=spy)
    provider = _BlockingProvider()
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))

    tasks = [asyncio.ensure_future(lane.complete(bound, make_request())) for _ in range(6)]
    await asyncio.sleep(0)  # Let every task queue; only 2 can hold a seat and reach the provider.
    assert len(provider.calls) == 2
    assert fanner.queued("fake") == 4

    provider.release()
    await asyncio.gather(*tasks)
    assert len(provider.calls) == 6

    # Replay the full call_started/call_finished history: at no point were more than 2 in flight.
    in_flight = 0
    max_in_flight = 0
    for kind, _source_id, _provider in spy.calls:
        in_flight += 1 if kind == "started" else -1
        max_in_flight = max(max_in_flight, in_flight)
    assert max_in_flight <= 2
    assert in_flight == 0  # Every started call was matched by a finished one.


async def test_fanner_lane_carries_grant_id_and_goal_id_onto_llm_call_when_given() -> None:
    fanner, trail = _build_fanner()
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("ok"))
    bound = make_bound(provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL), grant_id="grant_abc", goal_id="task_xyz")

    await lane.complete(bound, make_request())

    (event,) = await trail.query(TrailQuery())
    assert event.payload["grant_id"] == "grant_abc"
    assert event.payload["goal_id"] == "task_xyz"


async def test_fanner_lane_omits_grant_id_and_goal_id_when_the_lane_carries_none() -> None:
    fanner, trail = _build_fanner()
    provider = FakeLLMProvider(name="fake")
    provider.script(text_response("ok"))
    bound = make_bound(provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))  # No attribution.

    await lane.complete(bound, make_request())

    (event,) = await trail.query(TrailQuery())
    assert "grant_id" not in event.payload
    assert "goal_id" not in event.payload


async def test_a_call_cancelled_before_it_reaches_the_provider_gives_its_seat_back() -> None:
    # A sub-bee killed while its call's seat is held but the provider not yet called.
    recorder = _StuckStartRecorder()
    fanner, _ = _build_fanner(seats={"fake": 1}, recorder=recorder)
    provider = FakeLLMProvider(name="fake")
    bound = make_bound(binding="worker", provider=provider, model="test-model")
    lane = fanner.lane(_tempo(AccuracyBar.NORMAL))
    killed = asyncio.ensure_future(lane.complete(bound, make_request()))
    await asyncio.wait_for(recorder.entered.wait(), timeout=5.0)
    assert fanner.in_flight("fake") == 1

    killed.cancel()
    await asyncio.gather(killed, return_exceptions=True)

    assert fanner.in_flight("fake") == 0
    assert recorder.finished == []  # Nothing finished that never started.
