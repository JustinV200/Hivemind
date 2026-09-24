"""Test hivemind.entrance.streams.views: every live view is fed from durable state as it moves.

Over a real listener and a real Queen: the chat stream sends a line the moment it is written, the
live push channel carries a content-free notice to an attached device, and the security stream
carries every Entrance security event from the trail.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/views.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from builders.entrance.landing import LandingClient, LandingSession
from builders.entrance.serving import ProgramGrant, ServingRig, serving
from websockets.asyncio.client import ClientConnection, connect

_WAIT_S = 3.0  # Generous: every frame here follows a local write.
_EVERYTHING = ProgramGrant(
    capabilities=("observe", "entrance:submit", "entrance:push", "honey:clearance:c2")
)


async def _next(socket: ClientConnection) -> Any:  # noqa: ANN401 -- one parsed JSON frame.
    """The next frame, parsed."""
    async with asyncio.timeout(_WAIT_S):
        return json.loads(await socket.recv())


async def _open(
    rig: ServingRig, client: LandingClient, session: LandingSession, path: str
) -> ClientConnection:
    """Open ``path`` on loopback, send its signed first frame, and wait until it is served."""
    hub, live = rig.entrance.services.streams.hub, rig.entrance.services.push.live
    before = hub.subscribers
    socket = await connect(f"ws://127.0.0.1:{rig.entrance.listeners.loopback_port}{path}")
    await socket.send(client.hello(session, path))
    # Admitted and subscribed (to the trail, or to the live push hub) before the test writes.
    if path == "/v1/push/stream":
        await rig.until(lambda: session.key.device_id in live.live_devices())
    else:
        await rig.until(lambda: hub.subscribers > before)
    return socket


async def test_the_chat_stream_sends_a_line_as_it_is_written() -> None:
    async with serving() as rig:
        client, session = await rig.program(_EVERYTHING)
        socket = await _open(rig, client, session, "/v1/chat/stream")
        try:
            await client.call(session, "POST", "/v1/chat", {"text": "Is the page done?"})
            frame = await _next(socket)
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
            notice = await _next(socket)
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
            async with asyncio.timeout(_WAIT_S):
                while "guard.entrance_invited" not in kinds:
                    kinds.add((await _next(socket))["event"]["kind"])
        finally:
            await socket.close()

    assert "guard.entrance_invited" in kinds
