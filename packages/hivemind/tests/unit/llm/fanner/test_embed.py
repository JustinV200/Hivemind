"""Tests for hivemind.llm.fanner.embed: embed_through_fanner (FannerLane.embed's own logic).

Mirrors `test_lane.py`'s own `_build_fanner`/`_tempo` helpers and its "hosted headroom" tests,
narrowed to the embed path's own rules (roadmap 7.1, ADR-0032): no spill on a rate limit, and a
same-model fallback only when the primary is unreachable.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/embed.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.embed for the module under test.
    - hivemind.llm.fanner.lane for FannerLane.embed, the thin delegate this module's logic backs.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from builders.forage import make_source
from builders.llm import make_bound_embedder, make_embed_request, make_embed_response

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelSource
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.embedding.capabilities import EmbeddingCapabilities
from hivemind.llm.embedding.models import EmbeddingRequest, EmbeddingResponse
from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.llm.fanner.lane import Fanner, FannerDeps
from hivemind.llm.fanner.recorder import TrailLlmEventRecorder
from hivemind.pheromone import LlmEvent, MemoryPheromoneTrail, TrailQuery
from waggle.clock import Clock, FakeClock
from waggle.ids import new_hive_id, new_node_id


@dataclass(slots=True)
class _ScriptedEmbeddingProvider:
    """An EmbeddingProvider whose `embed` pops a scripted FIFO queue of responses or errors."""

    name: str = "hosted"
    calls: int = 0
    _script: deque[EmbeddingResponse | Exception] = field(default_factory=deque)
    _clock: Clock = field(default_factory=FakeClock)

    @property
    def capabilities(self) -> EmbeddingCapabilities:
        return EmbeddingCapabilities(
            dimensions=2, max_batch=32, max_input_chars=8_000, normalized=True
        )

    def script(self, *items: EmbeddingResponse | Exception) -> None:
        self._script.extend(items)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.calls += 1
        item = self._script.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    async def health(self) -> ProviderHealth:
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())


@dataclass(slots=True)
class _BlockingEmbeddingProvider:
    """An EmbeddingProvider whose `embed` awaits an external asyncio.Event before returning.

    Mirrors `test_lane.py`'s own `_BlockingProvider`, so a test can hold a call open to observe
    the seat as held (`Fanner.in_flight`) before releasing it.
    """

    name: str = "fake"
    calls: int = 0
    _released: asyncio.Event = field(default_factory=asyncio.Event)
    _clock: Clock = field(default_factory=FakeClock)

    @property
    def capabilities(self) -> EmbeddingCapabilities:
        return EmbeddingCapabilities(
            dimensions=2, max_batch=32, max_input_chars=8_000, normalized=True
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.calls += 1
        await self._released.wait()
        return make_embed_response(vectors=((1.0, 0.0),), dimensions=2)

    async def health(self) -> ProviderHealth:
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())

    def release(self) -> None:
        self._released.set()


def _tempo(bar: AccuracyBar = AccuracyBar.NORMAL) -> Tempo:
    return Tempo(accuracy=bar)


def _build_fanner(
    sources: tuple[ModelSource, ...] = (),
    seats: dict[str, int] | None = None,
    clock: Clock | None = None,
) -> tuple[Fanner, MemoryPheromoneTrail]:
    """Wire a Fanner over a fresh ForageMap and a trail-backed recorder; mirrors test_lane.py."""
    active_clock = clock if clock is not None else FakeClock()
    trail = MemoryPheromoneTrail(active_clock)
    recorder = TrailLlmEventRecorder(
        trail, new_hive_id(active_clock), new_node_id(active_clock), "human", active_clock
    )
    deps = FannerDeps(
        map=ForageMap(sources, clock=active_clock),
        seats=seats if seats is not None else {},
        limits={},
        clock=active_clock,
        recorder=recorder,
    )
    return Fanner(deps), trail


async def test_embed_takes_and_releases_a_seat_and_records_one_llm_call() -> None:
    fanner, trail = _build_fanner(seats={"fake": 1})
    provider = _BlockingEmbeddingProvider()
    bound = make_bound_embedder(provider=provider)
    lane = fanner.lane(_tempo())

    call = asyncio.ensure_future(lane.embed(bound, make_embed_request()))
    await asyncio.sleep(0)  # Let the call reach and hold the seat.

    assert fanner.in_flight("fake") == 1  # The seat is held while the call is in flight.
    provider.release()
    response = await call

    assert fanner.in_flight("fake") == 0  # Released once the call returns.
    assert isinstance(response, EmbeddingResponse)
    events = await trail.query(TrailQuery())
    (event,) = events  # Exactly one recorded occurrence for this one call.
    assert isinstance(event, LlmEvent)
    assert event.kind == "llm.call"
    assert event.slot == "EMBEDDER"
    assert event.provider == "fake"
    assert event.usage is not None


async def test_embed_never_spills_even_when_a_same_model_fallback_exists() -> None:
    fanner, _trail = _build_fanner()
    primary = _ScriptedEmbeddingProvider(name="hosted")
    primary.script(RateLimitedError("hosted", retry_after_s=None))
    fallback_provider = _ScriptedEmbeddingProvider(name="local")
    fallback_provider.script(make_embed_response())
    fallback = make_bound_embedder(binding="local_embedder", provider=fallback_provider)
    bound = make_bound_embedder(binding="embedder", provider=primary, fallback=fallback)
    lane = fanner.lane(_tempo())

    # Unlike a chat spill, a RateLimitedError on the primary is never absorbed by the fallback.
    with pytest.raises(RateLimitedError):
        await lane.embed(bound, make_embed_request())

    assert primary.calls == 1
    assert fallback_provider.calls == 0  # Never tried: embeddings never spill (ADR-0032).


async def test_embed_walks_a_same_model_fallback_when_the_primary_is_unreachable() -> None:
    # The EmbedGate contract: every same-model fallback is tried before an outage reaches the
    # caller, exactly as DirectEmbedGate does; the fallback's vectors stay comparable.
    fanner, trail = _build_fanner()
    primary = _ScriptedEmbeddingProvider(name="hosted")
    primary.script(ProviderUnavailableError("hosted", "connection refused"))
    fallback_provider = _ScriptedEmbeddingProvider(name="local")
    fallback_provider.script(make_embed_response())
    fallback = make_bound_embedder(binding="local_embedder", provider=fallback_provider)
    bound = make_bound_embedder(binding="embedder", provider=primary, fallback=fallback)
    lane = fanner.lane(_tempo())

    response = await lane.embed(bound, make_embed_request())

    assert isinstance(response, EmbeddingResponse)
    assert (primary.calls, fallback_provider.calls) == (1, 1)
    events = await trail.query(TrailQuery())
    assert [e.provider for e in events if isinstance(e, LlmEvent) and e.kind == "llm.call"] == [
        "local"
    ]


async def test_embed_reraises_an_outage_when_no_fallback_is_left() -> None:
    fanner, _trail = _build_fanner()
    primary = _ScriptedEmbeddingProvider(name="hosted")
    primary.script(ProviderUnavailableError("hosted", "connection refused"))
    lane = fanner.lane(_tempo())

    with pytest.raises(ProviderUnavailableError):
        await lane.embed(make_bound_embedder(provider=primary), make_embed_request())

    assert fanner.in_flight("hosted") == 0  # The failed attempt still released its seat.


async def test_a_rate_limited_error_throttles_the_source_and_reraises() -> None:
    clock = FakeClock()
    source = make_source(source_id="src_1", provider="hosted", model="test-embed", grade=5)
    fanner, trail = _build_fanner(sources=(source,), clock=clock)
    provider = _ScriptedEmbeddingProvider(name="hosted")
    provider.script(RateLimitedError("hosted", retry_after_s=45.0))
    bound = make_bound_embedder(binding="embedder", provider=provider, model="test-embed")
    lane = fanner.lane(_tempo())

    with pytest.raises(RateLimitedError):
        await lane.embed(bound, make_embed_request())

    abundance = fanner.deps.map.get("src_1").abundance
    assert abundance.throttled_until == clock.now() + timedelta(seconds=45.0)
    events = await trail.query(TrailQuery())
    (throttled_event,) = [e for e in events if e.kind == "llm.throttled"]
    assert throttled_event.payload["source_id"] == "src_1"
    assert throttled_event.payload["wait_s"] == 45.0
    assert all(e.kind != "llm.call" for e in events)  # The failed call is never recorded as one.
