"""Speak to a serving rig's Entrance as a device does: clips, push-to-talk, a question to answer.

Voice at the Landing Board (roadmap step 10.5f) is exercised over the builders' serving rig with a
``FakeTranscription`` whose ``calls`` prove when a model ran and when it did not. ``voice_rig``
builds the rig's options with the fake and any ``[entrance.voice]`` settings a test needs, and
``SPEAKER`` and ``PHONE`` are what a program and an interactive device that speak are granted;
``speak`` sends one clip on ``POST /v1/chat/audio`` with its intent and query; ``talk`` holds the
talk button on an open chat socket (chunks, then the end frame) and ``voice_reply`` reads that
socket until its answer arrives; ``ask_the_human`` runs a goal until its Warden asks the human a
question, the way the inbox route's own tests do; ``drain`` runs the Queen's intake once, and
``eventually`` waits, bounded, on the Hive's own state.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance/voice.

Key invariants:
    - Every wait is bounded by ``VOICE_WAIT_S``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import httpx
from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import RIG_SECTION, ProgramGrant, RigOptions, ServingRig
from websockets.asyncio.client import ClientConnection

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider, FakeTranscription
from hivemind.manifest.schema.entrance import EntranceVoiceSection
from hivemind.queen.ticks.human.intake import drain_goal_requests
from waggle.ids import TaskId, new_message_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Question

AUDIO = "/v1/chat/audio"  # The voice route.
# What a device that speaks holds: giving work, answering, and the C2 clearance the transcript
# it is answered with needs; a phone is the same, with a person typing at it.
SPEAKING = ("entrance:submit", "entrance:answer", "honey:clearance:c2")
SPEAKER = ProgramGrant(capabilities=SPEAKING)
PHONE = ProgramGrant(capabilities=SPEAKING, interactive=True)
CHAT_STREAM = "/v1/chat/stream"  # The socket push-to-talk speaks on.
VOICE_WAIT_S = 5.0  # Generous: every step here is local.
QUESTION_TEXT = "Which flower should the haiku praise?"  # What ask_the_human's Warden asks.
_REPLY_TYPES = frozenset({"voice", "voice_refused"})  # A push-to-talk hold's answers.

__all__ = [
    "AUDIO",
    "CHAT_STREAM",
    "PHONE",
    "QUESTION_TEXT",
    "SPEAKER",
    "SPEAKING",
    "VOICE_WAIT_S",
    "ask_the_human",
    "drain",
    "eventually",
    "speak",
    "talk",
    "voice_reply",
    "voice_rig",
]


def voice_rig(
    fake: FakeTranscription, provider: FakeLLMProvider | None = None, **voice: object
) -> RigOptions:
    """Return a rig's options serving voice through ``fake``, with ``voice`` settings applied.

    Args:
        fake: The transcriber; its ``calls`` show every clip a model heard.
        provider: The Queen's scripted model, for tests that plan.
        **voice: ``[entrance.voice]`` fields to set (``confirm_goals``, ``keep_audio``, ...).

    Returns:
        The options.
    """
    section = EntranceVoiceSection.model_validate({**RIG_SECTION.voice.model_dump(), **voice})
    return RigOptions(
        section=RIG_SECTION.model_copy(update={"voice": section}),
        transcriber=fake,
        provider=provider,
    )


async def speak(
    client: LandingClient,
    session: LandingSession,
    clip: bytes,
    intent: str,
    **query: object,
) -> httpx.Response:
    """Send ``clip`` to ``POST /v1/chat/audio`` as ``intent``, as a raw body signed as sent.

    Args:
        client: The device's client.
        session: Its session.
        clip: The audio.
        intent: goal, chat or answer:<question id>.
        **query: More query parameters (``duration_s``, ``language``), and ``media_type`` for
            the body's label (WAV unless told).

    Returns:
        The response, whatever its status.
    """
    media_type = str(query.pop("media_type", "audio/wav"))
    target = f"{AUDIO}?{urlencode({'intent': intent, **query})}"
    # The clip is the body: the signature covers its SHA-256, exactly as a device's does.
    headers = client.signed_headers(session, "POST", target, clip)
    headers["Content-Type"] = media_type
    return await client.http.request("POST", target, content=clip, headers=headers)


async def talk(
    socket: ClientConnection,
    clip: bytes,
    intent: str,
    chunk_bytes: int = 8_192,
    **chunk: object,
) -> None:
    """Hold the talk button on an open chat socket: ``clip`` in slices, then the end frame.

    Args:
        socket: The chat socket, authenticated.
        clip: The recording.
        intent: What it is for.
        chunk_bytes: How many bytes each slice carries.
        **chunk: Members every slice carries besides its bytes (``media_type``, ``duration_s``).
    """
    media_type = chunk.pop("media_type", "audio/wav")
    for start in range(0, len(clip), chunk_bytes):
        data = base64.b64encode(clip[start : start + chunk_bytes]).decode("ascii")
        frame = {"type": "audio_chunk", "media_type": media_type, "data": data, **chunk}
        await socket.send(json.dumps(frame))
    await socket.send(json.dumps({"type": "audio_end", "intent": intent}))


async def voice_reply(socket: ClientConnection) -> Any:  # noqa: ANN401 -- one parsed JSON frame.
    """Read the chat socket until a push-to-talk answer arrives; return it, parsed."""
    async with asyncio.timeout(VOICE_WAIT_S):
        while True:
            frame = json.loads(await socket.recv())
            if frame.get("type") in _REPLY_TYPES:
                return frame


async def ask_the_human(rig: ServingRig) -> tuple[str, TaskId]:
    """Submit a goal and have its Warden ask the human a question; return its id and the task.

    The rig's Queen must be running (``rig.queen.run()``), so she routes the question.
    """
    goal_id = await rig.queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await rig.warden_end.wait_for_assignment()
    question = Question(
        question_id=new_message_id(rig.clock),
        task_id=goal_id,
        asked_by=rig.queen.wardens[0].warden_id,
        text=QUESTION_TEXT,
        options=(),
        clearance=WireHoneyClearance.C1,
        asked_at=rig.clock.now(),
    )
    await rig.warden_end.send(question)

    async def blocked() -> bool:
        return (await rig.deps.chamber.get(goal_id)).status is TaskStatus.BLOCKED

    await eventually(blocked)
    [pending] = await rig.queen.human_inbox.pending_questions(rig.deps.chamber)
    return pending.id, goal_id


async def eventually(check: Callable[[], Awaitable[bool]]) -> None:
    """Wait until ``check`` holds, polling briefly; fail after ``VOICE_WAIT_S``.

    Args:
        check: Reads the Hive's own state (a task's status, a goal request's).
    """
    async with asyncio.timeout(VOICE_WAIT_S):
        while True:
            if await check():
                return
            await asyncio.sleep(0.01)


async def drain(rig: ServingRig) -> None:
    """Run the Queen's intake once: hold, plan or settle every goal request as her tick would."""
    await drain_goal_requests(rig.deps, rig.queen.wardens)
