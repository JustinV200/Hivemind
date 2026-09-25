"""Provide SocketRegistry: every live WebSocket, so each one closes the moment its session ends.

ADR-0041: every socket of a session closes when the session ends for any reason (logout, expiry,
revocation, lock, reduction), and a reduction closes every socket on the remote listener within one
second. The registry is how the rest of the Entrance reaches a socket it did not open: each socket
registers a ``LiveSocket`` (its listener, device and session) while it is served, and the logout
route, the device offboarder and the Entrance Reducer close them by session, by device or by
listener. Closing only signals; the socket's own task sends the close frame at once, so nothing
here waits on a slow client, with one bounded exception: the Reducer's ``close_remote`` waits up
to ``REMOTE_CLOSE_WAIT_S`` for the remote sockets to go, because the remote listener stops next
and its own shutdown would otherwise close a socket as a restart (1012) instead of telling the
client it was reduced. The registry is the Reducer's ``StreamCloser``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Filled by the
    socket lifecycle (``hivemind.entrance.streams.socket``); closed through by the logout route,
    the composed offboarder and the Reducer. Calls into nothing outside this package.

Key invariants:
    - A socket is registered from the moment it is admitted until its task ends.
    - Closing is idempotent: the first reason wins, and a closed socket stays closed.

See Also:
    - hivemind.entrance.reducer for the ``StreamCloser`` seam this implements.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.session.models import Listener
from hivemind.entrance.streams.errors import CloseReason
from waggle.ids import DeviceId

REMOTE_CLOSE_WAIT_S = 0.25  # How long a reduction waits for remote sockets to send their close.

log = get_logger(__name__)

__all__ = ["REMOTE_CLOSE_WAIT_S", "LiveSocket", "SocketRegistry"]


@dataclass(eq=False)
class LiveSocket:
    """One admitted WebSocket: where it came from, whose session it rides, and its close signal.

    Attributes:
        listener: The listener it arrived on.
        device_id: The device whose session admitted it.
        token_hash: That session's token hash (never the token).
        reason: Why it was told to close; None while it may run.
    """

    listener: Listener
    device_id: DeviceId
    token_hash: str = field(repr=False)
    reason: CloseReason | None = None
    _closing: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _gone: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def close(self, reason: CloseReason) -> None:
        """Tell the socket to close; the first reason given is the one sent.

        Args:
            reason: Why.
        """
        if self.reason is None:
            self.reason = reason
        self._closing.set()

    async def wait_closed(self) -> CloseReason:
        """Wait until the socket is told to close.

        Returns:
            Why it must close.
        """
        await self._closing.wait()
        return self.reason or CloseReason.UNSUBSCRIBED

    def mark_gone(self) -> None:
        """Record that the socket's task ended (its close frame, if any, was sent)."""
        self._gone.set()

    async def wait_gone(self) -> None:
        """Wait until the socket's task has ended."""
        await self._gone.wait()


class SocketRegistry:
    """Every live socket, closable by session, device or listener; the Reducer's StreamCloser."""

    def __init__(self) -> None:
        """Start with no socket."""
        # Mutated only on the event loop's thread, by socket tasks and the routes closing them.
        self._sockets: set[LiveSocket] = set()

    def open(self, listener: Listener, device_id: DeviceId, token_hash: str) -> LiveSocket:
        """Register an admitted socket.

        Args:
            listener: The listener it arrived on.
            device_id: The device whose session admitted it.
            token_hash: That session's token hash.

        Returns:
            Its handle; ``discard`` it when its task ends.
        """
        socket = LiveSocket(listener, device_id, token_hash)
        self._sockets.add(socket)
        return socket

    def discard(self, socket: LiveSocket) -> None:
        """Forget a socket whose task ended; forgetting one twice changes nothing.

        Args:
            socket: Its handle.
        """
        self._sockets.discard(socket)
        socket.mark_gone()

    def count(self, listener: Listener | None = None) -> int:
        """Count live sockets, on one listener or on both.

        Args:
            listener: Only this listener's; None for every socket.

        Returns:
            How many are registered.
        """
        return sum(1 for s in self._sockets if listener is None or s.listener is listener)

    def close_session(
        self, token_hash: str, reason: CloseReason = CloseReason.SESSION_ENDED
    ) -> int:
        """Close every socket of one session (logout).

        Args:
            token_hash: The session's token hash.
            reason: Why.

        Returns:
            How many sockets were told to close.
        """
        return self._close([s for s in self._sockets if s.token_hash == token_hash], reason)

    def close_device(
        self, device_id: DeviceId, reason: CloseReason = CloseReason.SESSION_ENDED
    ) -> int:
        """Close every socket of one device (lock, revocation, expiry).

        Args:
            device_id: The device.
            reason: Why.

        Returns:
            How many sockets were told to close.
        """
        return self._close([s for s in self._sockets if s.device_id == device_id], reason)

    def close_listener(self, listener: Listener, reason: CloseReason) -> int:
        """Close every socket on one listener.

        Args:
            listener: The listener.
            reason: Why.

        Returns:
            How many sockets were told to close.
        """
        return self._close([s for s in self._sockets if s.listener is listener], reason)

    def close_all(self, reason: CloseReason = CloseReason.SHUTTING_DOWN) -> int:
        """Close every socket (the Entrance is stopping).

        Args:
            reason: Why.

        Returns:
            How many sockets were told to close.
        """
        return self._close(list(self._sockets), reason)

    async def close_remote(self) -> None:
        """Close every socket on the remote listener: the Entrance Reducer's ``StreamCloser``.

        Waits up to ``REMOTE_CLOSE_WAIT_S`` for them to go (module docstring); a socket slower
        than that is closed by the listener's own shutdown instead.
        """
        closing = [s for s in self._sockets if s.listener is Listener.REMOTE]
        self._close(closing, CloseReason.REDUCED)
        try:
            # External wait: each socket's own task sending its close frame, bounded.
            async with asyncio.timeout(REMOTE_CLOSE_WAIT_S):
                for socket in closing:
                    await socket.wait_gone()
        except TimeoutError:
            log.warning("entrance.remote_sockets_slow", count=len(closing))

    def _close(self, sockets: list[LiveSocket], reason: CloseReason) -> int:
        """Signal each socket; its own task sends the close frame at once."""
        for socket in sockets:
            socket.close(reason)
        return len(sockets)
