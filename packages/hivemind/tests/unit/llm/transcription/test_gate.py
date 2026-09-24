"""Tests for hivemind.llm.transcription.gate: the TranscriptionGate seam and its direct gate.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/gate.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.gate for the module under test.
"""

from __future__ import annotations

import pytest
from builders.audio import make_tone_clip

from hivemind.forage.slots import ModelSlot
from hivemind.llm.errors import ProviderRequestError, ProviderUnavailableError, RateLimitedError
from hivemind.llm.transcription.binding import BoundTranscriber
from hivemind.llm.transcription.fake import FakeTranscription
from hivemind.llm.transcription.gate import DirectTranscriptionGate, TranscriptionGate
from hivemind.llm.transcription.models import Transcript


def _bound(
    provider: FakeTranscription, fallback: BoundTranscriber | None = None
) -> BoundTranscriber:
    """Bind `provider` as one link of a TRANSCRIBER chain."""
    return BoundTranscriber(
        slot=ModelSlot.TRANSCRIBER,
        binding=provider.name,
        provider=provider,
        model="speech-model",
        fallback=fallback,
    )


def _said(text: str) -> Transcript:
    """Build a transcript of `text` with no segments."""
    return Transcript(text=text, duration_s=1.0)


async def test_direct_gate_calls_the_first_link_and_passes_the_language_through() -> None:
    primary = FakeTranscription(name="primary")
    primary.script(_said("hello"))
    gate: TranscriptionGate = DirectTranscriptionGate()

    transcript = await gate.transcribe(_bound(primary), make_tone_clip(), "en")

    assert transcript.text == "hello"
    assert primary.calls[0].language == "en"


@pytest.mark.parametrize(
    "failure",
    [ProviderUnavailableError("primary", "down"), RateLimitedError("primary", retry_after_s=1.0)],
)
async def test_direct_gate_moves_to_the_fallback_on_an_outage_or_rate_limit(
    failure: ProviderUnavailableError | RateLimitedError,
) -> None:
    primary = FakeTranscription(name="primary")
    primary.script(failure)
    backup = FakeTranscription(name="backup")
    backup.script(_said("from the backup"))

    transcript = await DirectTranscriptionGate().transcribe(
        _bound(primary, _bound(backup)), make_tone_clip()
    )

    assert transcript.text == "from the backup"


async def test_direct_gate_raises_the_last_links_error_when_the_chain_runs_out() -> None:
    primary = FakeTranscription(name="primary")
    primary.set_outage(True)

    with pytest.raises(ProviderUnavailableError):
        await DirectTranscriptionGate().transcribe(_bound(primary), make_tone_clip())


async def test_direct_gate_never_retries_a_refused_request_on_the_fallback() -> None:
    primary = FakeTranscription(name="primary")
    primary.script(ProviderRequestError("primary", 400, detail="bad audio"))
    backup = FakeTranscription(name="backup")

    with pytest.raises(ProviderRequestError):
        await DirectTranscriptionGate().transcribe(
            _bound(primary, _bound(backup)), make_tone_clip()
        )
    assert backup.calls == []
