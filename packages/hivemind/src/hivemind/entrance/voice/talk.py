"""Read push-to-talk on the chat socket: bounded holds of audio frames, each heard on its end frame.

A device that holds a talk button streams its recording on its own chat socket (``/v1/chat/stream``,
roadmap step 10.5f): ``audio_chunk`` frames in order, then one ``audio_end`` frame naming the
intent. ``listen`` is that socket's reader, in place of the plain one that ignores whatever a client
sends: it gathers a hold's chunks in a bounded buffer (a count, the transcription boundary's byte
ceiling, and a deadline of ``max_clip_seconds`` plus a grace, with an idle limit between frames),
joins them with ``clip_from_chunks`` on the end frame, re-judges the session as it stands now (a
device locked or revoked mid-hold is refused before anything is heard), charges the hold like any
request, and hears the clip on the same path as ``POST /v1/chat/audio``. The answer goes back on
this socket alone: a ``voice`` frame with what the words became, or a ``voice_refused`` frame
carrying the status and ``ErrorBody`` the HTTP route would answer with. A hold that breaks the
protocol (a chunk too many, a format it does not take, an end with no audio, a deadline missed)
is refused once and dropped; the next chunk starts a new hold.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Run by the chat
    view (``hivemind.entrance.streams.views.landing``) as its socket's reader. Calls into the gate
    (``police``, ``error_body``), the session book, the transcription boundary and the voice door.

Key invariants:
    - A hold never buffers more than ``MAX_HOLD_CHUNKS`` frames or ``MAX_CLIP_BYTES`` bytes.
    - Every wait for a frame inside a hold is bounded; outside one, the reader waits as the plain
      reader does, until the client leaves.
    - Nothing here logs a frame, a chunk or a transcript.

See Also:
    - hivemind.entrance.voice.door for how a clip is heard.
    - hivemind.entrance.streams.socket for the socket lifecycle this runs inside.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError
from starlette.websockets import WebSocket, WebSocketDisconnect

from hivemind.common.errors import HiveMindError
from hivemind.entrance.auth.session import AuthenticatedSession
from hivemind.entrance.errors import AuthenticationFailedError
from hivemind.entrance.gate.admit import Caller, police
from hivemind.entrance.gate.handlers import INVALID_REQUEST_CODE, ErrorBody, error_body
from hivemind.entrance.gate.services import EntranceServices
from hivemind.entrance.gate.spec import session_with
from hivemind.entrance.voice.door import Utterance, hear, voice_of
from hivemind.entrance.voice.errors import UNPROCESSABLE, ClipRefusedError, PushToTalkError
from hivemind.entrance.voice.intent import SUBMIT, VoiceIntent
from hivemind.entrance.voice.models import (
    AudioChunkFrame,
    AudioEndFrame,
    VoiceAccepted,
    VoiceFrame,
    VoiceRefusedFrame,
    read_client_frame,
)
from hivemind.guard import CapabilitySet
from hivemind.llm.transcription import (
    MAX_CLIP_BYTES,
    AudioChunk,
    AudioMediaType,
    InvalidAudioClipError,
    clip_from_chunks,
)

MAX_HOLD_CHUNKS = 4_096  # Frames in one hold: quarter-second slices for seventeen minutes.
CHUNK_IDLE_S = 10.0  # A hold whose next frame is later than this was abandoned: it is dropped.
HOLD_GRACE_S = 10.0  # Beyond max_clip_seconds, how long a hold may last before it is dropped.
_BAD_FRAME = "This frame is not a push-to-talk frame the chat socket takes."
_TEXT_ONLY = "The chat socket takes text frames only: send audio as base64 in audio_chunk frames."
_VOICE_ACCESS = session_with(SUBMIT, c2=True)  # What each hold is policed with, as the route is.
_GONE = (WebSocketDisconnect, RuntimeError, OSError)  # A socket the client already left.

# Sends one frame on the shared socket, under its sending lock; False when the socket is gone.
Send = Callable[[BaseModel], Awaitable[bool]]

__all__ = ["CHUNK_IDLE_S", "HOLD_GRACE_S", "MAX_HOLD_CHUNKS", "Send", "Talk", "listen"]


@dataclass(frozen=True, slots=True)
class Talk:
    """The socket push-to-talk is read from: who is talking, what the Entrance offers, the reply.

    Attributes:
        websocket: The chat socket, admitted.
        caller: Its admitted session, as it stood when the socket opened.
        services: The Entrance's services.
        send: Sends a frame on the socket under its sending lock (the chat view shares it).
    """

    websocket: WebSocket
    caller: Caller
    services: EntranceServices
    send: Send


@dataclass(slots=True)
class _Hold:
    """One push-to-talk hold in progress: its chunks so far, bounded, and when it must end.

    Owned by one ``listen`` call; nothing else reads or changes it.
    """

    chunks: list[AudioChunk] = field(default_factory=list)
    size: int = 0  # Bytes buffered so far.
    started_at: float | None = None  # Monotonic seconds at its first chunk; None when idle.
    refused: bool = False  # Refused once already: its remaining frames are dropped unread.

    def reset(self) -> None:
        """End the hold, letting go of everything it buffered."""
        self.chunks.clear()
        self.size, self.started_at, self.refused = 0, None, False


async def listen(talk: Talk) -> None:
    """Read the client's frames until it leaves, hearing each push-to-talk hold as it ends.

    Args:
        talk: The socket, its caller, the Entrance's services and how to answer.
    """
    hold = _Hold()
    while True:
        try:
            message = await _next_message(talk, hold)
        except TimeoutError:
            # A hold that went quiet, or ran past its deadline: refused once, then dropped.
            await _refuse(talk, PushToTalkError("the hold ended without its end frame in time"))
            hold.reset()
            continue
        if message is None:
            return  # The client left; the view closes the socket.
        text = message.get("text")
        if isinstance(text, str):
            await _on_text(talk, hold, text)
        else:
            await _refuse_body(talk, ErrorBody(error=INVALID_REQUEST_CODE, detail=_TEXT_ONLY))


async def _next_message(talk: Talk, hold: _Hold) -> dict[str, object] | None:
    """Wait for the next message: unbounded between holds, within the hold's limits during one."""
    limit = _time_left(talk.services, hold)
    try:
        # External wait: the client's next frame; bounded only while a hold is open.
        async with asyncio.timeout(limit):
            message = await talk.websocket.receive()
    except _GONE:
        return None
    if message.get("type") == "websocket.disconnect":
        return None
    return dict(message)


def _time_left(services: EntranceServices, hold: _Hold) -> float | None:
    """Return how long the next frame of an open hold may take; None outside a hold."""
    if hold.started_at is None:
        return None
    longest = services.voice.rules.max_clip_seconds if services.voice is not None else 0.0
    elapsed = services.clock.monotonic() - hold.started_at
    return max(0.0, min(CHUNK_IDLE_S, longest + HOLD_GRACE_S - elapsed))


async def _on_text(talk: Talk, hold: _Hold, text: str) -> None:
    """Handle one text frame: a chunk joins the hold, an end frame is heard."""
    try:
        frame = read_client_frame(text)
    except ValidationError:
        # A frame that is not ours ends the hold it came in: the recording can no longer be whole.
        hold.reset()
        await _refuse_body(talk, ErrorBody(error=INVALID_REQUEST_CODE, detail=_BAD_FRAME))
        return
    if isinstance(frame, AudioChunkFrame):
        await _add_chunk(talk, hold, frame)
    else:
        await _end_hold(talk, hold, frame)


async def _add_chunk(talk: Talk, hold: _Hold, frame: AudioChunkFrame) -> None:
    """Buffer one chunk within the hold's bounds; refuse the hold once when it breaks one."""
    if hold.refused:
        return  # Already refused: its frames are dropped unread until its end frame.
    if hold.started_at is None:
        hold.started_at = talk.services.clock.monotonic()
    try:
        _check_bounds(talk.services, hold, frame)
        chunk = AudioChunk(
            data=frame.data,
            media_type=AudioMediaType.parse(frame.media_type),
            duration_s=frame.duration_s,
        )
    except (HiveMindError, ValidationError) as exc:
        # Refused once; what it buffered goes now, and the rest of it is dropped unread.
        hold.chunks.clear()
        hold.refused = True
        await _refuse(talk, exc)
        return
    hold.chunks.append(chunk)
    hold.size += len(frame.data)


def _check_bounds(services: EntranceServices, hold: _Hold, frame: AudioChunkFrame) -> None:
    """Refuse a chunk on a Hive without voice, or one that would break the hold's bounds."""
    voice_of(services)
    if len(hold.chunks) >= MAX_HOLD_CHUNKS:
        raise PushToTalkError(f"a hold carries at most {MAX_HOLD_CHUNKS} frames")
    if hold.size + len(frame.data) > MAX_CLIP_BYTES:
        raise PushToTalkError(f"a hold carries at most {MAX_CLIP_BYTES} bytes of audio")


async def _end_hold(talk: Talk, hold: _Hold, frame: AudioEndFrame) -> None:
    """Hear the hold that just ended, and answer this socket with what it became."""
    refused, chunks = hold.refused, list(hold.chunks)
    hold.reset()
    if refused:
        return  # Refused already when it broke a bound; this end frame closes it quietly.
    try:
        result = await _hear_hold(talk, chunks, frame)
    except (HiveMindError, ValidationError) as exc:
        await _refuse(talk, exc)
        return
    await talk.send(VoiceFrame(result=result))


async def _hear_hold(talk: Talk, chunks: list[AudioChunk], frame: AudioEndFrame) -> VoiceAccepted:
    """Build the hold's clip, re-judge its session as it stands now, and hear it."""
    services = talk.services
    voice_of(services)
    intent = VoiceIntent.parse(frame.intent)
    if not chunks:
        raise PushToTalkError("the hold ended before any audio")
    try:
        clip = await clip_from_chunks(_replay(chunks))
    except InvalidAudioClipError as exc:
        raise ClipRefusedError.from_invalid(exc) from exc
    current = await _current(talk.caller, services)
    # Each hold is a request: rate-limited, travel-locked and capability-checked like one.
    await police(services, current, _VOICE_ACCESS)
    return await hear(services, current, Utterance(clip, intent, frame.language))


async def _current(caller: Caller, services: EntranceServices) -> Caller:
    """Return the socket's caller as its session stands now; refuse one that has ended."""
    book = services.listeners[caller.listener].auth.sessions
    now = services.clock.now()
    # Latency: one or two local reads; a dead session is ended (and recorded) on the way.
    live = await book.live(caller.session.session.token_hash, now)
    if live is None:
        raise AuthenticationFailedError()
    session = AuthenticatedSession(
        session=live.session,
        device=live.device,
        capabilities=CapabilitySet.parse(*live.device.capabilities),
        stepped_up=live.session.stepped_up_at(now),
    )
    return Caller(session, caller.arrival)


async def _replay(chunks: list[AudioChunk]) -> AsyncIterator[AudioChunk]:
    """Yield a finished hold's chunks, in order, to ``clip_from_chunks``."""
    for chunk in chunks:
        yield chunk


async def _refuse(talk: Talk, error: Exception) -> None:
    """Send one refusal frame, shaped as the HTTP route would answer ``error``."""
    if isinstance(error, HiveMindError):
        status, body = error_body(error)
        await talk.send(VoiceRefusedFrame(status=status, refusal=body))
        return
    await _refuse_body(talk, ErrorBody(error=INVALID_REQUEST_CODE, detail=_BAD_FRAME))


async def _refuse_body(talk: Talk, body: ErrorBody) -> None:
    """Send a refusal frame for a frame the socket cannot read (422)."""
    await talk.send(VoiceRefusedFrame(status=UNPROCESSABLE, refusal=body))
