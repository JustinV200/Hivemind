"""Open one Landing Board view over a WebSocket, authenticated by its signed first frame.

The Hive Entrance pushes, clients do not poll (codingrules 8.15): the chat, the push channel and
the security events each have a live view at ``/v1/.../stream``. A socket carries no headers a
browser could set, so it authenticates with its first frame instead: the session's token and the
binding key's signature over ``hive-ws-v1``, the path and query exactly as requested, a timestamp
and a nonce, sent within five seconds of opening (ADR-0041). ``open_view`` opens the socket (the
same origin and TLS as the device's HTTP client, and never through an environment proxy), sends
that frame, and yields a ``View`` whose ``next`` reads one frame parsed as the view's model. A
socket the Entrance closes says why with a code (``hivemind.entrance.streams.CloseReason``), which
``ViewClosedError`` carries, so a follower can tell "fell behind, reconnect from the cursor" from
"session ended" from "this device may not read this view".

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Used by ``hivemind.cli.remote``'s
    goal follower. Calls into ``websockets``, ``hivemind.cli.landing.signing``, ``.transport``
    and ``.client``.

Key invariants:
    - Nothing but the signed first frame is sent on a view.
    - Every wait on a socket is bounded by the caller's timeout or ``FRAME_TIMEOUT_S``.

See Also:
    - hivemind.entrance.streams.socket for the server half of this lifecycle.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "A WebSocket
      authenticates with a first frame".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from hivemind.cli.landing.client import SignedIn
from hivemind.cli.landing.errors import (
    EntranceUnreachableError,
    LandingError,
    LandingProtocolError,
)
from hivemind.cli.landing.signing import Stamp, first_frame

OPEN_TIMEOUT_S = 10.0  # The handshake; a listener that has not accepted by then is not there.
FRAME_TIMEOUT_S = 30.0  # The longest one read waits when its caller gave no bound of its own.
FELL_BEHIND = 4409  # CloseReason.FELL_BEHIND: reconnect from the cursor.
FORBIDDEN = 4403  # CloseReason.FORBIDDEN: the device lacks the view's capability.
SESSION_ENDED = 4410  # CloseReason.SESSION_ENDED: logout, expiry, lock or revocation.

__all__ = [
    "FELL_BEHIND",
    "FORBIDDEN",
    "FRAME_TIMEOUT_S",
    "OPEN_TIMEOUT_S",
    "SESSION_ENDED",
    "View",
    "ViewClosedError",
    "open_view",
]


class ViewClosedError(LandingError):
    """Raise when the Entrance closed a view; carries its close code and reason text.

    Attributes:
        close_code: The WebSocket close code (4409 fell behind, 4410 session ended, ...).
    """

    def __init__(self, target: str, close_code: int | None, reason: str) -> None:
        """Build the error for one closed view.

        Args:
            target: The view's path.
            close_code: The close code the Entrance sent, or None when the socket just dropped.
            reason: The close frame's reason text (identifier-free by the Entrance's rule).
        """
        said = f": {reason}" if reason else ""
        super().__init__(f"The Entrance closed {target} (code {close_code}{said}).")
        self.close_code = close_code


class View:
    """One open, authenticated view: read its frames one at a time."""

    def __init__(self, socket: ClientConnection, target: str) -> None:
        """Hold the socket and what it was opened on.

        Args:
            socket: The open socket, its first frame already sent.
            target: The view's path and query, for messages.
        """
        self._socket = socket
        self.target = target

    async def next[ModelT: BaseModel](
        self, model: type[ModelT], timeout_s: float = FRAME_TIMEOUT_S
    ) -> ModelT:
        """Wait for the next frame and parse it as ``model``.

        Args:
            model: The view's frame model (``ChatFrame``, ``PushNotice``, ...).
            timeout_s: The longest to wait for it.

        Returns:
            The frame.

        Raises:
            TimeoutError: No frame arrived in time (the view is still open).
            ViewClosedError: The Entrance closed the view.
            LandingProtocolError: The frame is not ``model``.
        """
        try:
            # External wait: the next frame, bounded by the caller's own patience.
            async with asyncio.timeout(timeout_s):
                text = await self._socket.recv(decode=True)
        except ConnectionClosed as closed:
            received = closed.rcvd
            code = received.code if received is not None else None
            raise ViewClosedError(
                self.target, code, received.reason if received is not None else ""
            ) from closed
        try:
            return model.model_validate_json(text)
        except ValidationError as exc:
            raise LandingProtocolError(
                f"A frame on {self.target} is not a {model.__name__} ({exc.error_count()} "
                "problem(s))."
            ) from exc


@asynccontextmanager
async def open_view(board: SignedIn, target: str) -> AsyncIterator[View]:
    """Open ``target`` as ``board``'s session, send its signed first frame, close it on exit.

    Args:
        board: The logged-in device.
        target: The view's path and query exactly as requested (``/v1/chat/stream?after=3``).

    Yields:
        The open view.

    Raises:
        EntranceUnreachableError: The socket could not be opened.
    """
    address = board.client.address
    url = f"{address.socket_origin}{target}"
    try:
        # proxy=None: a view is reached directly, like every Landing Board request.
        socket = await connect(
            url, ssl=address.ssl_context(), proxy=None, open_timeout=OPEN_TIMEOUT_S
        )
    except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as exc:
        raise EntranceUnreachableError(
            f"The view {target} at {address.origin} could not be opened ({type(exc).__name__})."
        ) from exc
    try:
        stamp = Stamp.now(board.client.clock)
        await socket.send(first_frame(board.session.credential, target, stamp))
        yield View(socket, target)
    finally:
        # Latency: a close handshake, bounded by the socket's own close_timeout.
        await socket.close()
