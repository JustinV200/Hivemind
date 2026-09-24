"""Serve one WebSocket view: authenticate its first frame, run the view, close it on time.

Every live view of the Hive Entrance shares one lifecycle (ADR-0033). The socket is accepted, then
must send a first frame within the deadline (``StreamServices.hello_deadline_s``: ADR-0033's
``SOCKET_HELLO_DEADLINE_S``, five seconds) carrying its session's token and a signature over
``hive-ws-v1``, the path, a timestamp and a nonce (a browser cannot set headers on a
socket); a browser session's ``Origin`` must be the Entrance's own. The session is then policed
like any request (rate, travel lock, the view's capability at the Entrance route point). While the
view runs, four things race and the first to finish closes the socket with its reason: the view
itself ending, the client leaving, the socket being told to close (logout, a lock or revocation of
its device, a reduction: the ``SocketRegistry``), and a watchdog that re-judges the session every
``SOCKET_RECHECK_S`` so an expired or idle session closes its sockets too. The client's frames are
read (and ignored) only to see it leave, unless the view is a ``Duplex``: then its own reader reads
them (push-to-talk audio on the chat view, roadmap step 10.5f) and ends when the client leaves.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Called by each
    view's endpoint. Calls into the session checks, the gate's ``police`` and the registry.

Key invariants:
    - Nothing but the first frame is read before the socket is authenticated, and exactly one
      reader reads the client's frames after it.
    - A socket is registered from admission until its task ends, and is always closed with a code.
    - No token, signature or frame content is logged.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Sessions are bound
      to a key, and every request is signed".
    - hivemind.entrance.streams.registry for how a socket is told to close.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass

from pydantic import BaseModel
from starlette.websockets import WebSocket, WebSocketDisconnect

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.session import SocketOpening, authenticate_websocket
from hivemind.entrance.errors import AuthenticationFailedError
from hivemind.entrance.gate.admit import Caller, arrival_of, police
from hivemind.entrance.gate.errors import (
    CapabilityDeniedError,
    RateLimitedError,
    StepUpRequiredError,
)
from hivemind.entrance.gate.services import EntranceServices, ListenerDeps
from hivemind.entrance.gate.spec import Access
from hivemind.entrance.streams.errors import CloseReason
from hivemind.entrance.streams.registry import LiveSocket

SOCKET_RECHECK_S = 5.0  # How often a socket re-judges its session: expiry and idling close it.
# What a send, a receive or a close on a socket the client already left raises.
_GONE = (WebSocketDisconnect, RuntimeError, OSError)

log = get_logger(__name__)

__all__ = ["SOCKET_RECHECK_S", "Duplex", "StreamContext", "View", "send_frame", "serve_socket"]


@dataclass(frozen=True, slots=True)
class StreamContext:
    """What a view runs with.

    Attributes:
        websocket: The accepted, authenticated socket.
        caller: Its admitted session.
        services: The Entrance's services.
        live: Its registry handle.
    """

    websocket: WebSocket
    caller: Caller
    services: EntranceServices
    live: LiveSocket


# Runs a view on an admitted socket and returns why it ended.
View = Callable[[StreamContext], Coroutine[object, object, CloseReason]]


@dataclass(frozen=True, slots=True)
class Duplex:
    """A view whose client sends frames the view reads, beside the frames it streams.

    Attributes:
        view: Streams frames to the client, as any view does.
        reader: Reads the client's frames after the first, and returns once the client leaves.
    """

    view: View
    reader: View


async def serve_socket(
    websocket: WebSocket,
    access: Access,
    view: View | Duplex,
    services: EntranceServices,
    here: ListenerDeps,
) -> None:
    """Accept, authenticate and run one view, and close the socket with the reason it ended.

    Args:
        websocket: The socket, not yet accepted.
        access: The view's declared access.
        view: The view to run once the socket is admitted; a ``Duplex`` also reads the client.
        services: The Entrance's services.
        here: The listener's dependencies.
    """
    await websocket.accept()
    caller = await _hello(websocket, access, services, here)
    if caller is None:
        return
    sockets = services.streams.sockets
    live = sockets.open(caller.listener, caller.device.id, caller.session.session.token_hash)
    try:
        context = StreamContext(websocket, caller, services, live)
        # One reader of the client's frames: a duplex view's own, or the one that only sees it go.
        streaming, reading = (
            (view.view(context), view.reader(context))
            if isinstance(view, Duplex)
            else (view(context), _client_left(websocket))
        )
        reason = await _first_reason(
            streaming, reading, live.wait_closed(), _watch_session(context, here)
        )
        await _close(websocket, reason)
    finally:
        # Forgotten only once its close frame was sent: the Reducer waits for exactly that.
        sockets.discard(live)


async def send_frame(websocket: WebSocket, frame: BaseModel) -> bool:
    """Send one frame as JSON text.

    Args:
        websocket: The socket.
        frame: The frame's model.

    Returns:
        True when sent; False when the socket is gone.
    """
    try:
        # Latency: one frame into a local socket buffer.
        await websocket.send_text(frame.model_dump_json())
    except _GONE:
        return False
    return True


async def _hello(
    websocket: WebSocket, access: Access, services: EntranceServices, here: ListenerDeps
) -> Caller | None:
    """Read and check the first frame; close the socket and return None when it fails."""
    arrival = arrival_of(websocket.client.host if websocket.client else None, here.listener)
    try:
        # ADR-0033: a socket that has not authenticated within the deadline is closed.
        async with asyncio.timeout(services.streams.hello_deadline_s):
            frame = await websocket.receive_text()
    except (TimeoutError, KeyError, *_GONE):
        await _close(websocket, CloseReason.AUTHENTICATION_FAILED)
        return None
    raw_path = websocket.scope.get("raw_path")
    opening = SocketOpening(
        first_frame=frame,
        raw_path=raw_path.decode("latin-1") if isinstance(raw_path, bytes) else websocket.url.path,
        raw_query=bytes(websocket.scope.get("query_string", b"")).decode("latin-1"),
        origin=websocket.headers.get("origin"),
        arrival=arrival,
    )
    return await _admit(websocket, opening, access, (services, here))


async def _admit(
    websocket: WebSocket,
    opening: SocketOpening,
    access: Access,
    bundle: tuple[EntranceServices, ListenerDeps],
) -> Caller | None:
    """Authenticate the opening and police the session; close the socket on any refusal."""
    services, here = bundle
    try:
        # Latency: one or two local reads and one nonce write in the Entrance tables.
        session = await authenticate_websocket(here.auth.sessions, opening, services.clock.now())
        caller = Caller(session, opening.arrival)
        await police(services, caller, access)
    except AuthenticationFailedError:
        await _close(websocket, CloseReason.AUTHENTICATION_FAILED)
        return None
    except (CapabilityDeniedError, RateLimitedError, StepUpRequiredError):
        await _close(websocket, CloseReason.FORBIDDEN)
        return None
    return caller


async def _first_reason(*runs: Coroutine[object, object, CloseReason]) -> CloseReason:
    """Run every coroutine; return the first one's reason and cancel the rest."""
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(run) for run in runs]
        finished = asyncio.Event()
        for task in tasks:
            task.add_done_callback(lambda _task: finished.set())
        await finished.wait()
        winner = next(task for task in tasks if task.done())
        # The others lose: a view waiting on the hub, a watchdog asleep, a client silent.
        for task in tasks:
            task.cancel()
    return winner.result()


async def _client_left(websocket: WebSocket) -> CloseReason:
    """Read (and ignore) client frames until the client disconnects."""
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return CloseReason.UNSUBSCRIBED
    except _GONE:
        return CloseReason.UNSUBSCRIBED


async def _watch_session(context: StreamContext, here: ListenerDeps) -> CloseReason:
    """Re-judge the socket's session periodically; its expiry or idling closes the socket."""
    token_hash = context.caller.session.session.token_hash
    clock = context.services.clock
    while True:
        # External wait: the recheck interval, on the Entrance's clock.
        await clock.sleep(SOCKET_RECHECK_S)
        # Latency: one or two local reads; a dead session is ended (and recorded) on the way.
        if await here.auth.sessions.live(token_hash, clock.now()) is None:
            return CloseReason.SESSION_ENDED


async def _close(websocket: WebSocket, reason: CloseReason) -> None:
    """Close the socket with the reason's code; a socket already gone is left as it is."""
    try:
        await websocket.close(code=reason.code, reason=reason.text)
    except _GONE:
        log.debug("entrance.socket_already_gone", reason=reason.name)
