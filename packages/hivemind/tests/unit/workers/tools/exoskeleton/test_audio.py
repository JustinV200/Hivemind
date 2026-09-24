"""Unit tests for hivemind.workers.tools.exoskeleton.audio: listen and say.

`listen` runs against a scripted FakeBuzz and, for a model that does not hear audio, a
FakeTranscription behind real `Ears` (a DirectTranscriptionGate over a TRANSCRIBER binding), so the
transcript a test asserts is the one the transcriber was asked for. `say` runs through the real
gate and surface like every other GUI action.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/audio.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.audio for the module under test.
"""

from __future__ import annotations

import base64
from pathlib import Path

from builders.audio import SAMPLE_RATE, silence_pcm, tone_pcm
from builders.llm import make_bound
from builders.workers import make_gui_context, proposals_of, run_tool

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton import Peripherals, Recording
from hivemind.exoskeleton.buzz import FakeBuzz
from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    AudioPart,
    BoundTranscriber,
    DirectTranscriptionGate,
    Ears,
    FakeLLMProvider,
    FakeTranscription,
    ProviderCapabilities,
    ProviderUnavailableError,
    Transcript,
)
from hivemind.pheromone import TrailQuery
from hivemind.supervision.capping import RiskTier
from hivemind.workers.context import WorkerContext
from waggle.clock import FakeClock
from waggle.messages.capping import GuiOp, GuiStep

_DEAF = ProviderCapabilities.full().model_copy(update={"audio": False})
_TONE = Recording.from_pcm(tone_pcm(0.5), SAMPLE_RATE, 1)  # What the Cell's speaker plays.


class _Ears:
    """A FakeBuzz, a FakeTranscription behind real Ears, and a context holding both."""

    def __init__(self, *, hears_audio: bool = False) -> None:
        clock = FakeClock()
        self.session = FakeSession(scratch_dir=Path("scratch"), clock=clock)
        self.buzz = FakeBuzz(self.session)
        self.transcriber = FakeTranscription()
        bound = BoundTranscriber(
            slot=ModelSlot.TRANSCRIBER,
            binding="transcriber",
            provider=self.transcriber,
            model="speech-model",
        )
        capabilities = ProviderCapabilities.full() if hears_audio else _DEAF
        self.ctx: WorkerContext = make_gui_context(
            Peripherals(buzz=self.buzz),
            clock,
            session=self.session,
            bound=make_bound(provider=FakeLLMProvider(capabilities=capabilities)),
            ears=Ears(gate=DirectTranscriptionGate(), bound=bound),
        )


async def test_listen_hands_a_deaf_model_what_the_transcriber_heard() -> None:
    ears = _Ears()
    ears.buzz.script(_TONE)
    ears.transcriber.script(Transcript(text="  hello bees  ", language="en", duration_s=0.5))

    result = await run_tool(ears.ctx, "listen", {"seconds": 0.5})

    assert result.text == "Heard 0.5s of audio (en). Transcript: hello bees"
    assert result.media == ()
    (call,) = ears.transcriber.calls
    assert call.duration_s == _TONE.duration_s


async def test_listen_hands_a_model_that_hears_the_recording_itself() -> None:
    ears = _Ears(hears_audio=True)
    ears.buzz.script(_TONE)

    result = await run_tool(ears.ctx, "listen", {"seconds": 0.5})

    (clip,) = result.media
    assert isinstance(clip, AudioPart)
    assert clip.media_type == "audio/wav"
    assert base64.b64decode(clip.data_base64) == _TONE.wav
    assert result.text == "Recorded 0.5s, attached."
    assert ears.transcriber.calls == []  # No transcription for a model that hears.


async def test_listen_to_silence_says_there_was_no_speech() -> None:
    ears = _Ears()
    ears.buzz.script(Recording.from_pcm(silence_pcm(1.0), SAMPLE_RATE, 1))

    result = await run_tool(ears.ctx, "listen", {"seconds": 1.0})

    assert result.text == "Heard 1.0s of audio with no speech in it."


async def test_listen_refuses_a_duration_out_of_range_before_recording() -> None:
    ears = _Ears()

    result = await run_tool(ears.ctx, "listen", {"seconds": 600})

    assert result.text.startswith("invalid argument: seconds must be between")
    assert ears.transcriber.calls == []


async def test_a_transcriber_outage_is_a_readable_failure_not_a_crash() -> None:
    ears = _Ears()
    ears.transcriber.script(ProviderUnavailableError("fake", "down for the test"))

    result = await run_tool(ears.ctx, "listen", {"seconds": 0.5})

    assert result.text.startswith("listen failed: the transcriber failed:")


async def test_listen_never_proposes_anything() -> None:
    ears = _Ears()

    await run_tool(ears.ctx, "listen", {"seconds": 0.5})

    kinds = [event.kind for event in await ears.ctx.trail.query(TrailQuery())]
    assert not [kind for kind in kinds if kind.startswith("capping.")]


async def test_say_proposes_one_say_step_at_scratch_write_and_the_buzz_plays_it() -> None:
    ears = _Ears()
    await ears.session.put_file(Path("hello.wav"), _TONE.wav)

    result = await run_tool(ears.ctx, "say", {"clip": "hello.wav"})

    assert result.text.startswith("state=VERIFIED")
    (proposal,) = await proposals_of(ears.ctx)
    assert proposal.action.gui == (GuiStep(op=GuiOp.SAY, clip="hello.wav"),)
    assert proposal.risk_tier is RiskTier.SCRATCH_WRITE
    assert [path.name for path in ears.buzz.said] == ["hello.wav"]


async def test_say_refuses_a_missing_clip_before_anything_is_proposed() -> None:
    ears = _Ears()

    result = await run_tool(ears.ctx, "say", {"clip": "nowhere.wav"})

    assert result.text == "invalid argument: clip: there is no clip at that path"
    assert await proposals_of(ears.ctx) == []
    assert ears.ctx.telemetry.take_pending_alarms() == ()
