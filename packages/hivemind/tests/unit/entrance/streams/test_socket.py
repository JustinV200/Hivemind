"""Test hivemind.entrance.streams.socket: a socket authenticates first, and closes on time.

ADR-0041 over a real listener: the first frame is the session's token and a signature over the
socket's opening; anything else closes the socket as unauthenticated, a browser's socket from a
foreign origin too, and so does sending nothing until the first-frame deadline (shortened here
through the Entrance's settings), a device without the view's capability is closed as forbidden,
and ending the session (a logout) closes its sockets with that reason.

Fits into the Hive:
    Mirrors src/hivemind/entrance/streams/socket.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import json
import time

from builders.entrance.serving import ProgramGrant, RigOptions, ServingRig, serving
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from hivemind.entrance.streams import CloseReason

_SECURITY = "/v1/entrance/stream"  # Any observing device may open it.
_WAIT_S = 3.0  # Generous: every close here is immediate.
_SHORT_DEADLINE_S = 0.2  # The first-frame deadline a test waits out, instead of ADR-0041's 5 s.


async def _close_code(socket: ClientConnection) -> int | None:
    """Read until the server closes the socket; return its close code."""
    try:
        async with asyncio.timeout(_WAIT_S):
            while True:
                await socket.recv()
    except ConnectionClosed as closed:
        return closed.rcvd.code if closed.rcvd is not None else None


def _url(rig: ServingRig, path: str) -> str:
    """The loopback listener's WebSocket URL for ``path``."""
    return f"ws://127.0.0.1:{rig.entrance.listeners.loopback_port}{path}"


async def test_a_first_frame_that_is_not_a_signed_hello_closes_the_socket() -> None:
    async with serving() as rig, connect(_url(rig, _SECURITY)) as socket:
        await socket.send(
            json.dumps({"token": "nope", "timestamp": 0, "nonce": "x", "signature": "y"})
        )

        code = await _close_code(socket)

    assert code == CloseReason.AUTHENTICATION_FAILED.code


async def test_a_socket_that_sends_nothing_is_closed_at_the_first_frame_deadline() -> None:
    async with (
        serving(RigOptions(hello_deadline_s=_SHORT_DEADLINE_S)) as rig,
        connect(_url(rig, _SECURITY)) as socket,
    ):
        opened = time.monotonic()

        code = await _close_code(socket)
        waited = time.monotonic() - opened
        admitted = rig.entrance.services.streams.sockets.count()

    assert code == CloseReason.AUTHENTICATION_FAILED.code
    # Not at once (the server's clock started at accept, a moment before ours), and not at 5 s.
    assert _SHORT_DEADLINE_S / 2 <= waited < _WAIT_S
    assert admitted == 0


async def test_a_browser_socket_from_a_foreign_origin_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.browser(ProgramGrant(capabilities=("observe",)))
        async with connect(_url(rig, _SECURITY), origin=Origin("https://evil.example")) as socket:
            await socket.send(client.hello(session, _SECURITY))

            code = await _close_code(socket)

    assert code == CloseReason.AUTHENTICATION_FAILED.code


async def test_a_browser_socket_from_its_own_origin_is_admitted() -> None:
    async with serving() as rig:
        client, session = await rig.browser(ProgramGrant(capabilities=("observe",)))
        origin = Origin(rig.loopback_origin)
        async with connect(_url(rig, _SECURITY), origin=origin) as socket:
            hub = rig.entrance.services.streams.hub
            before = hub.subscribers
            await socket.send(client.hello(session, _SECURITY))
            await rig.until(lambda: hub.subscribers > before)
            console, console_session = await rig.console_session()
            await console.call(console_session, "POST", "/v1/entrance/invites", {"label": "tv"})

            async with asyncio.timeout(_WAIT_S):
                frame = json.loads(await socket.recv())

    assert frame["type"] == "security_event"


async def test_a_device_without_the_views_capability_is_closed_as_forbidden() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:push",)))
        async with connect(_url(rig, _SECURITY)) as socket:
            await socket.send(client.hello(session, _SECURITY))

            code = await _close_code(socket)

    assert code == CloseReason.FORBIDDEN.code


async def test_logging_out_closes_the_sessions_sockets() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        async with connect(_url(rig, _SECURITY)) as socket:
            await socket.send(client.hello(session, _SECURITY))
            # Admitted and registered before the session ends.
            await rig.until(lambda: rig.entrance.services.streams.sockets.count() == 1)

            logout = await client.call(session, "POST", "/v1/auth/logout")
            code = await _close_code(socket)

    assert logout.status_code == 204
    assert code == CloseReason.SESSION_ENDED.code
