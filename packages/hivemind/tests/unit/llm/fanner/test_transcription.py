"""Tests for hivemind.llm.fanner.transcription: FannerTranscriptionGate.

Fits into the Hive:
    Mirrors src/hivemind/llm/fanner/transcription.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.fanner.transcription for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from builders.audio import make_tone_clip
from builders.forage import make_source
from builders.llm import make_bound, make_request, text_response

from hivemind.forage.map import ForageMap
from hivemind.forage.models import ModelSource
from hivemind.forage.slots import ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm.capabilities import HealthState, ProviderHealth
from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.llm.fake import FakeLLMProvider
from hivemind.llm.fanner.lane import Fanner, FannerDeps
from hivemind.llm.fanner.recorder import TrailLlmEventRecorder
from hivemind.llm.fanner.transcription import FannerTranscriptionGate
from hivemind.llm.transcription import (
    AudioChunk,
    AudioClip,
    BoundTranscriber,
    FakeTranscription,
    Transcript,
    TranscriptionCapabilities,
    TranscriptionGate,
    TranscriptSegment,
)
from hivemind.pheromone import LlmEvent, MemoryPheromoneTrail, PheromoneEvent, TrailQuery
from waggle.clock import Clock, FakeClock
from waggle.ids import new_hive_id, new_node_id

_NORMAL = Tempo(accuracy=AccuracyBar.NORMAL)


@dataclass
class _BlockingTranscriber:
    """A transcriber whose calls wait on an asyncio.Event, to hold a seat open on purpose."""

    name: str = "speech"
    calls: int = 0
    _released: asyncio.Event = field(default_factory=asyncio.Event)
    _clock: Clock = field(default_factory=FakeClock)

    @property
    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities()

    async def transcribe(self, clip: AudioClip, language: str | None = None) -> Transcript:
        self.calls += 1
        await self._released.wait()
        return Transcript(text="held", duration_s=clip.duration_s)

    def stream(
        self, chunks: AsyncIterator[AudioChunk], language: str | None = None
    ) -> AsyncIterator[TranscriptSegment]:
        raise NotImplementedError("not used by these tests")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(state=HealthState.HEALTHY, detail="ok", checked_at=self._clock.now())

    def release(self) -> None:
        """Unblock every waiting (and future) call."""
        self._released.set()


def _build(
    sources: tuple[ModelSource, ...] = (),
    seats: dict[str, int] | None = None,
    clock: FakeClock | None = None,
) -> tuple[Fanner, MemoryPheromoneTrail]:
    """Wire a Fanner over a fresh map and a trail-backed recorder, sharing one clock."""
    active = clock if clock is not None else FakeClock()
    trail = MemoryPheromoneTrail(active)
    recorder = TrailLlmEventRecorder(
        trail, new_hive_id(active), new_node_id(active), "human", active
    )
    deps = FannerDeps(
        map=ForageMap(sources, clock=active),
        seats=seats if seats is not None else {},
        limits={},
        clock=active,
        recorder=recorder,
    )
    return Fanner(deps), trail


def _link(
    provider: FakeTranscription | _BlockingTranscriber,
    binding: str = "transcriber",
    fallback: BoundTranscriber | None = None,
) -> BoundTranscriber:
    """Bind `provider` as one TRANSCRIBER link serving "speech-model"."""
    return BoundTranscriber(
        slot=ModelSlot.TRANSCRIBER,
        binding=binding,
        provider=provider,
        model="speech-model",
        fallback=fallback,
    )


def _said(text: str) -> Transcript:
    """A transcript of `text` over the first half-second."""
    segment = TranscriptSegment(start_s=0.0, end_s=0.5, text=text)
    return Transcript.from_segments([segment], language="en", duration_s=1.0)


async def _events(trail: MemoryPheromoneTrail, kind: str) -> list[PheromoneEvent]:
    """Every recorded event of `kind`, in order."""
    return [event for event in await trail.query(TrailQuery()) if event.kind == kind]


async def test_the_gate_records_one_llm_call_with_audio_seconds_and_no_text() -> None:
    fanner, trail = _build()
    speech = FakeTranscription(name="speech")
    speech.script(_said("private words"))
    gate: TranscriptionGate = FannerTranscriptionGate(fanner, _NORMAL)
    clip = make_tone_clip(1.0)

    transcript = await gate.transcribe(_link(speech), clip, "en")

    assert transcript.text == "private words"
    (event,) = await _events(trail, "llm.call")
    assert isinstance(event, LlmEvent)
    assert (event.slot, event.provider) == ("TRANSCRIBER", "speech")
    assert event.usage is not None and event.usage.input_tokens == event.usage.output_tokens == 0
    assert event.payload["model"] == "speech-model"
    assert event.payload["audio_seconds"] == clip.duration_s
    assert "latency_s" in event.payload
    assert "private words" not in event.model_dump_json()


async def test_the_gate_carries_grant_and_goal_ids_when_given() -> None:
    fanner, trail = _build()
    gate = FannerTranscriptionGate(fanner, _NORMAL, grant_id="grant_1", goal_id="task_1")

    await gate.transcribe(_link(FakeTranscription(name="speech")), make_tone_clip())

    (event,) = await _events(trail, "llm.call")
    assert (event.payload["grant_id"], event.payload["goal_id"]) == ("grant_1", "task_1")


async def test_a_transcription_takes_its_seat_from_the_same_meter_chat_calls_use() -> None:
    fanner, _ = _build(seats={"speech": 1})
    holder = _BlockingTranscriber()
    gate = FannerTranscriptionGate(fanner, _NORMAL)
    chat = FakeLLMProvider(name="speech")
    chat.script(text_response("after"))

    held = asyncio.ensure_future(gate.transcribe(_link(holder), make_tone_clip()))
    await asyncio.sleep(0)  # The transcription takes the provider's only seat and blocks.
    queued = asyncio.ensure_future(
        fanner.lane(_NORMAL).complete(make_bound(provider=chat), make_request())
    )
    await asyncio.sleep(0)  # The chat call reaches the same meter and queues behind it.

    assert (fanner.in_flight("speech"), fanner.queued("speech")) == (1, 1)
    holder.release()
    await held
    assert (await queued).text == "after"


async def test_the_gate_updates_the_sources_latency_and_free_seats_on_the_map() -> None:
    source = make_source(source_id="ears", provider="speech", model="speech-model", seats=2)
    fanner, _ = _build(sources=(source,))

    await FannerTranscriptionGate(fanner, _NORMAL).transcribe(
        _link(FakeTranscription(name="speech")), make_tone_clip()
    )

    updated = fanner.deps.map.get("ears")
    assert updated.distance is not None and updated.distance.tokens_per_s == 0.0
    assert updated.abundance.seats_free == 1  # DEFAULT_SEATS: one seat, now free again.


async def test_the_gate_spills_past_a_source_below_the_tempos_grade_floor() -> None:
    weak = make_source(source_id="weak", provider="speech", model="speech-model", grade=1)
    fanner, trail = _build(sources=(weak,))
    primary, backup = FakeTranscription(name="speech"), FakeTranscription(name="backup")
    backup.script(_said("from the backup"))
    critical = Tempo(accuracy=AccuracyBar.CRITICAL)

    transcript = await FannerTranscriptionGate(fanner, critical).transcribe(
        _link(primary, fallback=_link(backup, binding="ears")), make_tone_clip()
    )

    assert transcript.text == "from the backup"
    assert primary.calls == []
    (spill,) = await _events(trail, "llm.spill")
    assert spill.payload["reason"] == "GRADE_BELOW_FLOOR"
    assert (spill.payload["from_binding"], spill.payload["to_binding"]) == ("transcriber", "ears")


async def test_a_rate_limit_throttles_the_source_and_moves_to_the_fallback() -> None:
    source = make_source(source_id="ears", provider="speech", model="speech-model")
    fanner, trail = _build(sources=(source,))
    primary, backup = FakeTranscription(name="speech"), FakeTranscription(name="backup")
    primary.script(RateLimitedError("speech", retry_after_s=30.0))

    await FannerTranscriptionGate(fanner, _NORMAL).transcribe(
        _link(primary, fallback=_link(backup, binding="ears")), make_tone_clip()
    )

    assert fanner.deps.map.get("ears").abundance.throttled_until is not None
    (throttle,) = await _events(trail, "llm.throttled")
    assert throttle.payload["wait_s"] == 30.0
    assert len(backup.calls) == 1


async def test_an_outage_records_a_fallback_and_answers_from_the_next_link() -> None:
    fanner, trail = _build()
    primary, backup = FakeTranscription(name="speech"), FakeTranscription(name="backup")
    primary.set_outage(True)
    backup.script(_said("from the backup"))

    transcript = await FannerTranscriptionGate(fanner, _NORMAL).transcribe(
        _link(primary, fallback=_link(backup, binding="ears")), make_tone_clip()
    )

    assert transcript.text == "from the backup"
    (fallback,) = await _events(trail, "llm.fallback")
    assert fallback.payload["reason"] == "PROVIDER_UNAVAILABLE"
    assert fanner.in_flight("speech") == 0  # The failed attempt still released its seat.


@pytest.mark.parametrize(
    "failure", [ProviderUnavailableError("speech", "down"), RateLimitedError("speech")]
)
async def test_the_last_links_failure_reaches_the_caller(
    failure: ProviderUnavailableError | RateLimitedError,
) -> None:
    fanner, trail = _build()
    primary = FakeTranscription(name="speech")
    primary.script(failure)

    with pytest.raises(type(failure)):
        await FannerTranscriptionGate(fanner, _NORMAL).transcribe(_link(primary), make_tone_clip())
    assert await _events(trail, "llm.call") == []


async def test_the_gate_spills_when_the_seat_wait_eats_the_latency_budget() -> None:
    clock = FakeClock()
    fanner, trail = _build(seats={"speech": 1}, clock=clock)
    holder = _BlockingTranscriber()
    backup = FakeTranscription(name="backup")
    bound = _link(holder, fallback=_link(backup, binding="ears"))
    # A tight budget: any real queueing exceeds SPILL_WAIT_FRACTION of it.
    gate = FannerTranscriptionGate(
        fanner, Tempo(accuracy=AccuracyBar.NORMAL, latency_budget_s=0.001)
    )

    held = asyncio.ensure_future(gate.transcribe(bound, make_tone_clip()))
    await asyncio.sleep(0)  # Takes the only seat and blocks.
    waiting = asyncio.ensure_future(gate.transcribe(bound, make_tone_clip()))
    await asyncio.sleep(0)  # Queues behind it.
    clock.advance(1.0)
    holder.release()
    await held
    await waiting

    assert len(backup.calls) == 1
    reasons = [event.payload["reason"] for event in await _events(trail, "llm.spill")]
    assert reasons == ["QUEUE_WAIT_EXCEEDED"]
