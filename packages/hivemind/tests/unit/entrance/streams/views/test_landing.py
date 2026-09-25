"""Test hivemind.entrance.streams.views.landing: the Landing Board's own views, fed as state moves.

Over a real listener and a real Queen: the chat stream sends a line the moment it is written, the
live push channel carries a content-free notice to an attached device, and the security stream
carries every Entrance security event from the trail. A notice sent to a socket whose client has
just left detaches it quietly (roadmap step 10.5c's guide test found it raising out of the push
outbox and stopping `hive serve`).

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views/landing.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from typing import cast

from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import ProgramGrant, ServingRig, serving
from builders.entrance.views import VIEW_WAIT_S, next_frame, open_view
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.asyncio.client import ClientConnection

from hivemind.entrance.push import DeliveryOutcome, LivePush, NoticeKind, PushNotice
from hivemind.entrance.streams.views import live_sender
from waggle.clock import SystemClock
from waggle.ids import new_device_id

_EVERYTHING = ProgramGrant(
    capabilities=("observe", "entrance:submit", "entrance:push", "honey:clearance:c2")
)


async def _open(
    rig: ServingRig, client: LandingClient, session: LandingSession, path: str
) -> ClientConnection:
    """Open ``path`` on loopback and wait until it is subscribed (to the trail, or live push)."""
    if path != "/v1/push/stream":
        return await open_view(rig, client, session, path)
    live = rig.entrance.services.push.live
    # The push channel's feed is its live hub: attached once the device is listed there.
    return await open_view(
        rig, client, session, path, lambda: int(session.key.device_id in live.live_devices())
    )


async def test_the_chat_stream_sends_a_line_as_it_is_written() -> None:
    async with serving() as rig:
        client, session = await rig.program(_EVERYTHING)
        socket = await _open(rig, client, session, "/v1/chat/stream")
        try:
            await client.call(session, "POST", "/v1/chat", {"text": "Is the page done?"})
            frame = await next_frame(socket)
        finally:
            await socket.close()

    assert frame["type"] == "chat"
    assert (frame["entry"]["author"], frame["entry"]["text"]) == ("human", "Is the page done?")


async def test_the_live_push_channel_carries_a_content_free_notice() -> None:
    async with serving() as rig:
        client, session = await rig.program(_EVERYTHING)
        socket = await _open(rig, client, session, "/v1/push/stream")
        console, console_session = await rig.console_session()
        try:
            await console.call(console_session, "POST", "/v1/entrance/invites", {"label": "phone"})
            notice = await next_frame(socket)
        finally:
            await socket.close()

    assert notice["kind"] == "security_event"
    assert set(notice) == {"event_id", "kind", "ref", "created_at"}


async def test_the_security_stream_carries_entrance_events_from_the_trail() -> None:
    async with serving() as rig:
        client, session = await rig.program(_EVERYTHING)
        socket = await _open(rig, client, session, "/v1/entrance/stream")
        console, console_session = await rig.console_session()
        try:
            await console.call(console_session, "POST", "/v1/entrance/invites", {"label": "phone"})
            kinds = set()
            async with asyncio.timeout(VIEW_WAIT_S):
                while "guard.entrance_invited" not in kinds:
                    kinds.add((await next_frame(socket))["event"]["kind"])
        finally:
            await socket.close()

    assert "guard.entrance_invited" in kinds


class _LeftSocket:
    """A socket whose client already left: a send raises what Starlette raises for it then."""

    async def send_text(self, data: str) -> None:
        """Refuse the frame the way a socket whose client disconnected does."""
        raise WebSocketDisconnect(code=1006)


async def test_a_notice_to_a_socket_whose_client_left_detaches_it_and_raises_nothing() -> None:
    clock, gone, hub = SystemClock(), asyncio.Event(), LivePush()
    device_id = new_device_id(clock)
    hub.attach(device_id, live_sender(cast(WebSocket, _LeftSocket()), gone))
    notice = PushNotice.mint(NoticeKind.WITHDRAWN, "question_x", clock)

    outcome = await hub.deliver(notice, device_id)

    assert outcome is DeliveryOutcome.GONE
    assert gone.is_set() and device_id not in hub.live_devices()
