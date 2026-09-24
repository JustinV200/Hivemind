"""Define the Landing Board's own live views: the chat, the push channel, the security events.

Clients never poll (codingrules 8.11); each view is a WebSocket declared as a ``SocketSpec`` and
served through the shared lifecycle in ``hivemind.entrance.streams.socket``. ``/v1/chat/stream``
sends every new chat line after a cursor (``after``, or the newest line when absent): it re-reads
the Queen's chat log whenever the trail moves, and at least every ``CHAT_RESYNC_S``, since a line
may be appended just after the event that accompanies it. It is also where a device holds its talk
button (roadmap step 10.5f): the view is a ``Duplex`` whose reader is the voice package's
push-to-talk ``listen``, which answers each hold on this socket alone; one lock orders the two
writers, so a chat line and a voice answer never interleave mid-frame. ``/v1/push/stream`` is the
live push channel: the socket is attached to the ``LivePush`` hub and receives the same content-free
notices webhooks and Web Push carry. ``/v1/entrance/stream`` sends every Entrance security event
from the trail. Each subscription to the hub is bounded by the Entrance's stream backlog, so a
reader that lags is closed with FELL_BEHIND. The Hive's own views (trail, telemetry, Forage, tasks,
episodes, Cells) are the modules beside this one.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams.views``. Its
    ``LANDING_VIEWS`` join the route table through ``hivemind.entrance.streams.views.VIEWS``.
    Calls into the stream hub, the chat log, the live push hub and the voice package's
    push-to-talk reader.

Key invariants:
    - The chat view needs ``entrance:submit`` and ``honey:clearance:c2``, like reading the chat
      (and like speaking into it: a hold's answer carries the transcript).
    - A voice answer goes to the socket whose device spoke, never to another.
    - A push frame is a ``PushNotice``: it never carries content.
    - A notice sent to a socket whose client already left detaches that socket; nothing the send
      raises escapes into the push outbox.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "One stream per view".
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect

from hivemind.entrance.gate.params import Here, Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, SocketSpec, session_with
from hivemind.entrance.models import ChatFrame, SecurityFrame, chat_line, security_frame
from hivemind.entrance.push import LiveSender, LiveSocketClosedError, PushNotice
from hivemind.entrance.streams.errors import CloseReason, StreamClosedError
from hivemind.entrance.streams.socket import Duplex, StreamContext, send_frame, serve_socket
from hivemind.entrance.streams.views.pump import trail_subscription
from hivemind.entrance.voice import (
    AudioChunkFrame,
    AudioEndFrame,
    Send,
    Talk,
    VoiceFrame,
    VoiceRefusedFrame,
    listen,
)
from hivemind.pheromone import PheromoneEvent
from hivemind.queen import ChatQuery
from hivemind.queen.chat import MAX_CHAT_PAGE

CHAT_RESYNC_S = 2.0  # The chat log is re-read at least this often, whatever the trail does.
_CHAT_FAMILIES = frozenset({"queen", "task", "alarm"})  # Trail families a chat line comes with.
_DOOR_KINDS = frozenset({"guard.reduced", "guard.reopened", "guard.reduce_ordered"})
_ENTRANCE_PREFIX = "guard.entrance_"  # Every enrolled-device and session security event.
# What a send on a socket the client already left raises: Starlette's disconnect (a client that
# closed while a notice was in flight), or a reset connection, or a send after the close.
_GONE = (WebSocketDisconnect, OSError, RuntimeError)

_CHAT_ACCESS = session_with("entrance:submit", c2=True)
_PUSH_ACCESS = session_with("entrance:push")
_SECURITY_ACCESS = session_with("observe")

__all__ = [
    "CHAT_RESYNC_S",
    "LANDING_VIEWS",
    "chat_stream",
    "live_sender",
    "push_stream",
    "security_stream",
]


async def chat_stream(
    websocket: WebSocket,
    services: Services,
    here: Here,
    after: Annotated[int | None, Query(ge=0, description="Resume after this position.")] = None,
) -> None:
    """Stream every new chat line after a cursor (C2).

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
        after: The position to resume after; None starts at the newest line.
    """
    # The socket's two writers (new lines, voice answers) take turns: one frame at a time.
    sending = asyncio.Lock()

    async def send(frame: BaseModel) -> bool:
        """Send one frame under the socket's lock; False once the client is gone."""
        async with sending:
            return await send_frame(websocket, frame)

    async def view(context: StreamContext) -> CloseReason:
        """Send new lines whenever the trail moves, and at least every CHAT_RESYNC_S."""
        return await _follow_chat(context, after, send)

    async def talk(context: StreamContext) -> CloseReason:
        """Hear every push-to-talk hold the client sends, until it leaves."""
        await listen(Talk(context.websocket, context.caller, context.services, send))
        return CloseReason.UNSUBSCRIBED

    await serve_socket(websocket, _CHAT_ACCESS, Duplex(view, talk), services, here)


async def push_stream(websocket: WebSocket, services: Services, here: Here) -> None:
    """Attach the socket to the live push channel: content-free notices, as they happen.

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
    """
    await serve_socket(websocket, _PUSH_ACCESS, _live_push, services, here)


async def security_stream(websocket: WebSocket, services: Services, here: Here) -> None:
    """Stream every Entrance security event from the trail.

    Args:
        websocket: The socket.
        services: The Entrance's services.
        here: This listener's dependencies.
    """
    await serve_socket(websocket, _SECURITY_ACCESS, _follow_security, services, here)


def live_sender(websocket: WebSocket, gone: asyncio.Event) -> LiveSender:
    """Build the live push channel's sender for one admitted socket.

    A notice can be in flight while its device closes the socket; whatever the send then raises
    must detach this sender, never escape into the push outbox, which shares a task group with
    the Queen.

    Args:
        websocket: The admitted socket notices are written to.
        gone: Set when a send finds the client gone, which ends the view.

    Returns:
        What ``LivePush.attach`` takes: it writes one frame, and turns a lost socket into
        ``LiveSocketClosedError``, which the hub answers by detaching it.
    """

    async def send(frame: str) -> None:
        """Write one notice frame; a lost socket detaches this sender."""
        try:
            await websocket.send_text(frame)
        except _GONE as error:
            gone.set()
            raise LiveSocketClosedError(str(error)) from None

    return send


async def _follow_chat(context: StreamContext, after: int | None, send: Send) -> CloseReason:
    """Send chat lines after the cursor until the socket or the subscription closes."""
    chat = context.services.hive.chat
    cursor = after if after is not None else await _newest_seq(context)
    subscription = trail_subscription(context, "chat", _moves_the_chat)
    try:
        while True:
            # Latency: one local indexed read of the chat table.
            for entry in await chat.read(ChatQuery(after_seq=cursor, limit=MAX_CHAT_PAGE)):
                line = chat_line(entry)
                if not await send(ChatFrame(entry=line)):
                    return CloseReason.UNSUBSCRIBED
                cursor = line.seq
            try:
                # External wait: the next trail events, or the resync interval.
                async with asyncio.timeout(CHAT_RESYNC_S):
                    await subscription.next_batch()
            except TimeoutError:
                continue
    except StreamClosedError as closed:
        return closed.reason
    finally:
        subscription.close()


async def _newest_seq(context: StreamContext) -> int:
    """Return the chat's newest position, so a stream with no cursor sends only what is new."""
    # Latency: one local indexed read (the newest page, one line).
    newest = await context.services.hive.chat.read(ChatQuery(limit=1))
    if not newest:
        return 0
    return newest[-1].seq or 0


async def _live_push(context: StreamContext) -> CloseReason:
    """Attach to the live push hub and wait until the socket goes."""
    gone = asyncio.Event()
    live = context.services.push.live
    attachment = live.attach(context.caller.device.id, live_sender(context.websocket, gone))
    try:
        await gone.wait()
        return CloseReason.UNSUBSCRIBED
    finally:
        attachment.detach()


async def _follow_security(context: StreamContext) -> CloseReason:
    """Send every security event the hub delivers until something closes."""
    subscription = trail_subscription(context, "security", _is_security_event)
    try:
        while True:
            for event in await subscription.next_batch():
                if not await send_frame(context.websocket, security_frame(event)):
                    return CloseReason.UNSUBSCRIBED
    except StreamClosedError as closed:
        return closed.reason
    finally:
        subscription.close()


def _moves_the_chat(event: PheromoneEvent) -> bool:
    """Return whether ``event`` may come with a new chat line (a reply, a question, an Alarm)."""
    return event.kind.split(".", 1)[0] in _CHAT_FAMILIES


def _is_security_event(event: PheromoneEvent) -> bool:
    """Return whether ``event`` is an Entrance security event."""
    return event.kind.startswith(_ENTRANCE_PREFIX) or event.kind in _DOOR_KINDS


LANDING_VIEWS: tuple[SocketSpec, ...] = (
    SocketSpec(
        path="/v1/chat/stream",
        listeners=BOTH_LISTENERS,
        access=_CHAT_ACCESS,
        endpoint=chat_stream,
        summary="New chat lines after a cursor, as they are written (C2); push-to-talk in.",
        frame_model=ChatFrame,
        client_frames=(AudioChunkFrame, AudioEndFrame),
        reply_frames=(VoiceFrame, VoiceRefusedFrame),
    ),
    SocketSpec(
        path="/v1/push/stream",
        listeners=BOTH_LISTENERS,
        access=_PUSH_ACCESS,
        endpoint=push_stream,
        summary="The live push channel: content-free notices that something is waiting.",
        frame_model=PushNotice,
    ),
    SocketSpec(
        path="/v1/entrance/stream",
        listeners=BOTH_LISTENERS,
        access=_SECURITY_ACCESS,
        endpoint=security_stream,
        summary="Every Entrance security event, from the trail.",
        frame_model=SecurityFrame,
    ),
)
