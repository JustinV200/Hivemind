"""Test hivemind.entrance.voice.route: POST /v1/chat/audio over real listeners and a real Queen.

A clip arrives as a raw body with its media type (ADR-0040): a WAV is measured by its own header, a
compressed clip by the length its query declares, and every clip the Hive cannot take is refused
with its status before the transcriber (a ``FakeTranscription`` recording every call) hears
anything. While ``[entrance.voice]`` is off the route is never mounted: a 404 on both listeners.

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/route.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.audio import silent_wav
from builders.entrance.serving import RigOptions, serving
from builders.entrance.voice import AUDIO, SPEAKER, speak, voice_rig

from hivemind.llm.transcription import AudioMediaType, FakeTranscription

_HEARD = "Please tidy the garden notes into one page."  # What the fake transcriber hears.
_WAV = silent_wav(1.0)  # One second of 16 kHz 16-bit silence: a WAV the header measures.
_OPUS = b"OggS" + b"\x00" * 60  # An Ogg stream's first bytes; only its declared length counts.


async def test_a_clip_is_heard_once_and_its_transcript_goes_back_to_the_speaker() -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)

        spoken = await speak(client, session, _WAV, "chat", language="en-US")

    assert spoken.status_code == 202, spoken.text
    body = spoken.json()
    assert (body["intent"], body["transcript"], body["language"]) == ("chat", _HEARD, "en")
    assert body["duration_s"] == 1.0 and body["chat"]["id"].startswith("chat_")
    [call] = fake.calls
    assert (call.size_bytes, call.duration_s, call.language) == (len(_WAV), 1.0, "en")


async def test_a_compressed_clip_is_measured_by_the_length_its_query_declares() -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)

        spoken = await speak(
            client, session, _OPUS, "chat", duration_s=2.5, media_type="audio/ogg;codecs=opus"
        )

    assert spoken.status_code == 202, spoken.text
    [call] = fake.calls
    assert (call.media_type, call.duration_s) == (AudioMediaType.OGG_OPUS, 2.5)


async def test_a_clip_larger_than_any_json_body_is_still_read_whole() -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    long_wav = silent_wav(10.0)  # 320 KB: past the 256 KiB every JSON body is held to.
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)

        spoken = await speak(client, session, long_wav, "chat")

    assert len(long_wav) > 262_144
    assert spoken.status_code == 202, spoken.text
    assert fake.calls[0].duration_s == 10.0


@pytest.mark.parametrize(
    ("clip", "query", "status", "error"),
    [
        (_WAV, {"media_type": "audio/flac"}, 415, "hivemind.entrance.clip_refused"),
        (_OPUS, {"media_type": "audio/ogg"}, 422, "hivemind.entrance.clip_refused"),
        (b"not a wav at all", {}, 422, "hivemind.entrance.clip_refused"),
        (_WAV, {"stray": "1"}, 422, "hivemind.entrance.invalid_request"),
    ],
    ids=["unsupported-format", "compressed-without-length", "malformed-wav", "stray-query"],
)
async def test_a_clip_the_hive_cannot_take_is_refused_before_it_is_heard(
    clip: bytes, query: dict[str, object], status: int, error: str
) -> None:
    fake = FakeTranscription()
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)

        refused = await speak(client, session, clip, "chat", **query)

    assert refused.status_code == status, refused.text
    assert refused.json()["error"] == error
    assert fake.calls == []


async def test_a_clip_with_no_content_type_or_an_unknown_intent_is_refused() -> None:
    fake = FakeTranscription()
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)
        target = f"{AUDIO}?intent=chat"
        headers = client.signed_headers(session, "POST", target, _WAV)
        unlabelled = await client.http.post(target, content=_WAV, headers=headers)
        unknown = await speak(client, session, _WAV, "shout")

    assert unlabelled.status_code == 415, unlabelled.text
    assert unknown.status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize("serves", ["disabled", "no-transcriber"])
async def test_while_voice_is_off_the_route_is_never_mounted(serves: str) -> None:
    fake = FakeTranscription()
    options = voice_rig(fake, enabled=False) if serves == "disabled" else RigOptions()
    async with serving(options) as rig:
        client, session = await rig.program(SPEAKER)

        refused = await speak(client, session, _WAV, "chat")
        typed = await client.call(session, "POST", "/v1/chat", {"text": "still here"})

    assert refused.status_code == 404
    assert refused.json()["error"] == "hivemind.entrance.not_found"
    assert typed.status_code == 202  # The chat itself is untouched.
    assert fake.calls == []
