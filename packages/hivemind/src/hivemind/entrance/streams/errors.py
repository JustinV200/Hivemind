"""Define why a stream closes, and the error a closed subscription raises.

A live view of the Hive Entrance closes for a handful of reasons, and a client needs to tell them
apart without guessing: its session ended (log in again), the Entrance was reduced (the remote
listener is gone), it fell behind (reconnect and catch up from a cursor), the Entrance is shutting
down, or its first frame never authenticated. ``CloseReason`` names each one with the WebSocket
close code it is sent as (application codes 4000-4999, RFC 6455 section 7.4.2) and a short reason
text; ``StreamClosedError`` is what a closed subscription raises to the view reading it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.streams``. Used by the
    hub, the socket registry and every view. Calls into ``hivemind.entrance.errors`` only.

Key invariants:
    - Every reason has a distinct close code; a reason text never carries an identifier.

See Also:
    - hivemind.entrance.streams.socket for where a socket is closed with them.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.entrance.errors import EntranceError

__all__ = ["CloseReason", "StreamClosedError"]


class CloseReason(Enum):
    """Why a stream closed: a WebSocket close code and a short, identifier-free reason."""

    UNSUBSCRIBED = (1000, "closed")  # The view finished, or the client left.
    SHUTTING_DOWN = (1001, "the entrance is shutting down")  # The Hive is stopping.
    AUTHENTICATION_FAILED = (4401, "authentication failed")  # No valid first frame in time.
    FORBIDDEN = (4403, "forbidden")  # The device lacks the view's capability.
    FELL_BEHIND = (4409, "fell behind; reconnect from your cursor")  # Too slow to keep up.
    SESSION_ENDED = (4410, "session ended")  # Logout, expiry, idling out, lock, revocation.
    REDUCED = (4411, "the entrance was reduced")  # The Entrance Reducer closed every remote socket.

    @property
    def code(self) -> int:
        """The WebSocket close code sent for it."""
        return self.value[0]

    @property
    def text(self) -> str:
        """The close frame's reason text."""
        return self.value[1]


class StreamClosedError(EntranceError):
    """Raise from a subscription that closed; carries why."""

    code: ClassVar[str] = "hivemind.entrance.stream_closed"

    def __init__(self, reason: CloseReason) -> None:
        """Build the error.

        Args:
            reason: Why the subscription closed.
        """
        super().__init__(f"The stream closed: {reason.text}.")
        self.reason = reason
