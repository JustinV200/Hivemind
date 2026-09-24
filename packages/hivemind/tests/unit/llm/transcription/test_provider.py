"""Tests for hivemind.llm.transcription.provider: the TranscriptionProvider protocol.

TranscriptionProvider has no logic of its own (codingrules section 8.1: a Protocol is
structural), so this module covers what belongs to `provider.py` itself: that it cannot be
instantiated, and that `FakeTranscription` satisfies its whole call surface -- the typed
assignment below is itself a check `mypy --strict` enforces. The behavioural contract every
implementation must meet lives in `tests/contracts/test_transcription_provider_contract.py`.

Fits into the Hive:
    Mirrors src/hivemind/llm/transcription/provider.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.transcription.provider for the module under test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from builders.audio import make_chunks, make_tone_clip, tone_pcm

from hivemind.llm.capabilities import ProviderHealth
from hivemind.llm.transcription.capabilities import TranscriptionCapabilities
from hivemind.llm.transcription.fake import FakeTranscription
from hivemind.llm.transcription.models import AudioChunk, Transcript, TranscriptSegment
from hivemind.llm.transcription.provider import TranscriptionProvider


async def _iterate(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Yield `chunks` as a push-to-talk stream would deliver them."""
    for chunk in chunks:
        yield chunk


def test_transcription_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        TranscriptionProvider()  # type: ignore[misc]  # The instantiation itself is the test.


async def test_fake_transcription_satisfies_the_transcription_provider_protocol() -> None:
    fake = FakeTranscription()
    segment = TranscriptSegment(start_s=0.0, end_s=0.5, text="hi")
    fake.script(Transcript.from_segments([segment], language="en", duration_s=1.0))
    # The annotated assignment is part of the test: mypy fails it if the fake drifts.
    provider: TranscriptionProvider = fake

    assert isinstance(provider.name, str)
    assert isinstance(provider.capabilities, TranscriptionCapabilities)
    assert isinstance(await provider.transcribe(make_tone_clip(), "en"), Transcript)
    assert isinstance(await provider.health(), ProviderHealth)


async def test_fake_transcription_stream_returns_an_async_iterator_of_segments() -> None:
    fake = FakeTranscription()
    segment = TranscriptSegment(start_s=0.0, end_s=0.5, text="hi")
    fake.script(Transcript.from_segments([segment], language=None, duration_s=1.0))
    provider: TranscriptionProvider = fake

    segments = [item async for item in provider.stream(_iterate(make_chunks(tone_pcm())))]

    assert segments == [segment]
