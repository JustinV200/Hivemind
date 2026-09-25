"""Test hivemind.entrance.voice.talk: push-to-talk holds on the chat socket, heard on one path.

Over real listeners, a real Queen and a real chat socket: a hold's slices are joined in order and
heard once, and the answer (a ``voice`` frame) reaches the socket that spoke and no other, while
the chat line it made reaches every chat reader; a spoken goal is echoed back and held; a hold
past its bounds, a hold that goes quiet, a frame that is not push-to-talk, and an answer from a
device without ``entrance:answer`` are refused on the socket before any model runs, and the socket
stays open for the chat; with voice off a hold is refused as not served.

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/talk.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from builders.audio import silent_wav
from builders.entrance.serving import ProgramGrant, serving
from builders.entrance.views import next_frame, open_view
from builders.entrance.voice import (
    CHAT_STREAM,
    PHONE,
    SPEAKER,
    talk,
    voice_reply,
    voice_rig,
)
from websockets.asyncio.client import ClientConnection

from hivemind.entrance.voice import MAX_HOLD_CHUNKS
from hivemind.llm.transcription import AudioMediaType, FakeTranscription
from waggle.ids import new_message_id

_WAV = silent_wav(1.0)  # 32 KB: four slices of 8 KiB.
_HEARD = "Tell me how the bees are doing."
_GOAL = "Write a haiku about bees."


async def test_a_hold_is_joined_in_order_heard_once_and_answered_on_its_own_socket() -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    async with serving(voice_rig(fake)) as rig:
        speaker, session = await rig.program(SPEAKER)
        listener, listener_session = await rig.program(SPEAKER)
        mine = await open_view(rig, speaker, session, CHAT_STREAM)
        theirs = await open_view(rig, listener, listener_session, CHAT_STREAM)

        await talk(mine, _WAV, "chat")
        here = await _until_both(mine)
        there = await next_frame(theirs)
        await mine.close()
        await theirs.close()

    answer = here["voice"]
    result = answer["result"]
    assert (result["intent"], result["transcript"]) == ("chat", _HEARD)
    [call] = fake.calls
    assert (call.size_bytes, call.media_type) == (len(_WAV), AudioMediaType.WAV)
    # The chat line reaches every reader; the voice answer only the socket that spoke.
    assert here["chat"]["entry"]["id"] == result["chat"]["id"]
    assert there["type"] == "chat" and there["entry"]["text"] == _HEARD


async def test_a_spoken_goal_on_the_socket_is_echoed_back_and_held() -> None:
    fake = FakeTranscription()
    fake.script(_GOAL)
    async with serving(voice_rig(fake)) as rig:
        phone, session = await rig.program(PHONE)
        socket = await open_view(rig, phone, session, CHAT_STREAM)

        await talk(socket, _WAV, "goal")
        answer = await voice_reply(socket)
        await socket.close()

    goal = answer["result"]["goal"]
    assert (goal["state"], goal["source"]) == ("AWAITING_CONFIRMATION", "spoken")


async def test_a_compressed_hold_is_measured_by_its_slices_declared_lengths() -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    opus = {"media_type": "audio/webm;codecs=opus", "duration_s": 0.25}
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)
        socket = await open_view(rig, client, session, CHAT_STREAM)

        await talk(socket, b"\x1aE\xdf\xa3" + b"\x00" * 60, "chat", chunk_bytes=16, **opus)
        answer = await voice_reply(socket)
        await socket.close()

    assert answer["type"] == "voice"
    assert fake.calls[0].duration_s == 1.0  # Four quarter-second slices.


async def test_a_hold_past_its_frame_bound_is_refused_once_and_dropped() -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    too_many = b"\x00\x00" * (MAX_HOLD_CHUNKS + 1)  # One 2-byte slice past the bound.
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)
        socket = await open_view(rig, client, session, CHAT_STREAM)

        await talk(socket, too_many, "chat", chunk_bytes=2)
        refused = await voice_reply(socket)
        await talk(socket, _WAV, "chat")  # The next hold starts afresh.
        answer = await voice_reply(socket)
        await socket.close()

    assert (refused["type"], refused["status"]) == ("voice_refused", 422)
    assert refused["refusal"]["error"] == "hivemind.entrance.push_to_talk_refused"
    assert answer["type"] == "voice" and len(fake.calls) == 1


async def test_a_hold_that_goes_quiet_is_refused_once_and_the_next_hold_is_heard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    # A hold is let go when its next frame is CHUNK_IDLE_S late: a moment here, not ten seconds.
    monkeypatch.setattr("hivemind.entrance.voice.talk.CHUNK_IDLE_S", 0.2)
    slice_ = {"type": "audio_chunk", "media_type": "audio/wav"}
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)
        socket = await open_view(rig, client, session, CHAT_STREAM)

        # One slice of a hold, and then nothing: no more audio, no end frame.
        data = base64.b64encode(_WAV[:4_096]).decode("ascii")
        await socket.send(json.dumps({**slice_, "data": data}))
        quiet = await voice_reply(socket)
        await talk(socket, _WAV, "chat")
        answer = await voice_reply(socket)
        await socket.close()

    assert (quiet["status"], quiet["refusal"]["error"]) == (
        422,
        "hivemind.entrance.push_to_talk_refused",
    )
    assert answer["type"] == "voice" and len(fake.calls) == 1


async def test_frames_the_socket_does_not_take_are_refused_and_it_stays_open() -> None:
    fake = FakeTranscription()
    fake.script(_HEARD)
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)
        socket = await open_view(rig, client, session, CHAT_STREAM)

        await socket.send(b"\x00\x01binary")
        binary = await voice_reply(socket)
        await socket.send(json.dumps({"type": "audio_end", "intent": "shout"}))
        stranger = await voice_reply(socket)
        await socket.send(json.dumps({"type": "audio_end", "intent": "chat"}))
        empty = await voice_reply(socket)
        await talk(socket, _WAV, "chat")
        answer = await voice_reply(socket)
        await socket.close()

    assert [frame["status"] for frame in (binary, stranger, empty)] == [422, 422, 422]
    assert empty["refusal"]["error"] == "hivemind.entrance.push_to_talk_refused"
    assert answer["type"] == "voice" and len(fake.calls) == 1


async def test_an_answer_without_entrance_answer_is_refused_on_the_socket_before_hearing() -> None:
    fake = FakeTranscription()
    grant = ProgramGrant(capabilities=("entrance:submit", "honey:clearance:c2"))
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(grant)
        socket = await open_view(rig, client, session, CHAT_STREAM)

        await talk(socket, _WAV, f"answer:{new_message_id(rig.clock)}")
        refused = await voice_reply(socket)
        await socket.close()

    assert (refused["status"], refused["refusal"]["capability"]) == (403, "entrance:answer")
    assert fake.calls == []


async def test_with_voice_off_a_hold_is_refused_but_the_chat_socket_serves_on() -> None:
    fake = FakeTranscription()
    async with serving(voice_rig(fake, enabled=False)) as rig:
        client, session = await rig.program(SPEAKER)
        socket = await open_view(rig, client, session, CHAT_STREAM)

        await talk(socket, _WAV, "chat")
        refused = await voice_reply(socket)
        posted = await client.call(session, "POST", "/v1/chat", {"text": "typed instead"})
        line = await next_frame(socket)
        await socket.close()

    assert (refused["status"], refused["refusal"]["error"]) == (
        404,
        "hivemind.entrance.voice_not_served",
    )
    assert posted.status_code == 202 and line["entry"]["text"] == "typed instead"
    assert fake.calls == []


async def _until_both(socket: ClientConnection) -> dict[str, Any]:
    """Read frames until both a voice answer and a chat line arrived, in whichever order."""
    seen: dict[str, Any] = {}
    while not {"voice", "chat"} <= seen.keys():
        frame = await next_frame(socket)
        seen[frame["type"]] = frame
    return seen
