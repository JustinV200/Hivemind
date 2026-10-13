"""Apply a listener's per-connection limits and report every refusal they make.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) is carried by
transports that are policy-free, but the Hive Stand's one listener (the Hive Stand is the machine
the Queen, the central orchestrator, runs on) must also protect itself from the nodes that dial
it: a node is one process with its own signing key, and a Warden (the always-on supervisor of one
Cell) or a Pollen Packet (the thin gateway on an enrolled device) may be broken or taken over.
This module is the listener's two checks and the vocabulary around them. ``ServerLimits`` is the
configuration, with either check switchable off. ``ServerLimiter`` is the listener-wide half: it
owns the one ReplayWindow every connection shares, so a frame replayed on a fresh connection is as
visible as one replayed on the old, and hands each accepted connection its own
``ConnectionLimiter``, which holds that connection's FrameCeiling and answers the two questions
the WebSocket transport asks of its receive path: may this frame be read at all, and is this
decoded envelope a duplicate. Waggle writes no trail, so each refusal is handed to the caller's
``on_refusal`` callback as a ``ConnectionRefusal`` and the Hive Stand's listener wrapper records it.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Built by waggle.transport.websocket_server, whose
    accepted WebSocketTransports call the ConnectionLimiter through their ReceiveHooks; calls
    into waggle.transport.replay, waggle.transport.ceiling and waggle.clock.

Key invariants:
    - One ReplayWindow per ServerLimiter, shared by all its connections; one FrameCeiling per
      connection, never shared.
    - A check reports its refusal before it answers False, so a refusal is on record by the time
      anything is closed or dropped; a missing on_refusal makes the report a no-op, not a skip
      of the refusal itself.
    - The report is awaited under REFUSAL_REPORT_TIMEOUT_S, so a stuck recorder cannot hold a
      connection's receive path forever; the timeout propagates to the receiver, never swallowed.
    - Nothing here compares or branches on a refusal's kind: the kind is only recorded.

See Also:
    - docs/waggle/spec.md section 9 (the receiver obligation, close code 4029).
    - waggle.transport.websocket for ReceiveHooks, the protocol a ConnectionLimiter satisfies.
    - waggle.transport.replay and waggle.transport.ceiling for the two checks themselves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.transport.ceiling import DEFAULT_CEILING_LIMITS, CeilingLimits, FrameCeiling
from waggle.transport.replay import DEFAULT_REPLAY_LIMITS, ReplayLimits, ReplayWindow

# The most a refusal report may take. A recorder writes one trail event, local work measured in
# milliseconds; this bound only has to be generous enough never to trip on a busy disk.
REFUSAL_REPORT_TIMEOUT_S = 5.0

__all__ = [
    "REFUSAL_REPORT_TIMEOUT_S",
    "ConnectionLimiter",
    "ConnectionRefusal",
    "RefusalHandler",
    "RefusalKind",
    "ServerLimiter",
    "ServerLimits",
]


class RefusalKind(StrEnum):
    """Which limit refused: the two things a listener recognises and records."""

    REPLAY = "REPLAY"  # A decoded envelope whose (node_id, id) was already seen in the window.
    FRAME_CEILING = "FRAME_CEILING"  # A connection sent frames faster than its ceiling allows.


@dataclass(frozen=True, slots=True)
class ConnectionRefusal:
    """One refusal, as handed to the listener's ``on_refusal`` callback.

    Attributes:
        node_id: The ``node_id`` of the duplicate itself for a REPLAY, or of the last envelope
            the connection decoded for a FRAME_CEILING. None when the connection tripped its
            ceiling before it decoded a single envelope, so no node had been named yet.
        kind: Which limit refused.
        envelope_id: The duplicate's ``id`` for a REPLAY; None for a FRAME_CEILING, which
            concerns the connection and no particular envelope.
        at: When the refusal happened, read from the listener's injected Clock.
    """

    node_id: str | None
    kind: RefusalKind
    envelope_id: str | None
    at: datetime


RefusalHandler = Callable[[ConnectionRefusal], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ServerLimits:
    """The per-connection limits a listener enforces; None switches a check off.

    Attributes:
        replay: The shared replay window's bounds, or None to drop no duplicates.
        ceiling: Each connection's frame ceiling, or None to count no frames.
    """

    replay: ReplayLimits | None = DEFAULT_REPLAY_LIMITS
    ceiling: CeilingLimits | None = DEFAULT_CEILING_LIMITS


class ServerLimiter:
    """The listener-wide half of the limits: the shared replay window and a limiter per connection.

    Concurrency model: every method is synchronous; the replay window it owns is touched only
    from tasks on one event loop and never across an await.
    """

    def __init__(
        self, limits: ServerLimits, clock: Clock, on_refusal: RefusalHandler | None
    ) -> None:
        """Create the shared state a listener's connections will use.

        Args:
            limits: Which checks are on, and their bounds.
            clock: The injected Clock every check and every report reads time from.
            on_refusal: Awaited with a ConnectionRefusal each time a check refuses; None records
                nothing (the refusal is still enforced).
        """
        self._limits = limits
        self._clock = clock
        self._on_refusal = on_refusal
        # One window for the whole listener, never one per connection: a replay on a fresh
        # connection after a reconnect is the case the check exists for.
        self._replay = None if limits.replay is None else ReplayWindow(limits.replay)

    def for_connection(self) -> ConnectionLimiter:
        """Return the limiter for one newly accepted connection, with its own full bucket.

        Returns:
            A ConnectionLimiter sharing this listener's replay window.
        """
        ceiling = None if self._limits.ceiling is None else FrameCeiling(self._limits.ceiling)
        return ConnectionLimiter(ceiling, self._replay, self._clock, self._on_refusal)


class ConnectionLimiter:
    """One connection's two checks, in the shape the WebSocket transport's receive path calls.

    Satisfies ``waggle.transport.websocket.ReceiveHooks`` structurally. Concurrency model: the one
    task that receives on the connection is its only caller.
    """

    def __init__(
        self,
        ceiling: FrameCeiling | None,
        replay: ReplayWindow | None,
        clock: Clock,
        on_refusal: RefusalHandler | None,
    ) -> None:
        """Bind one connection's ceiling to the listener's shared replay window.

        Args:
            ceiling: This connection's own bucket, or None to count no frames.
            replay: The listener's shared window, or None to drop no duplicates.
            clock: The injected Clock every check and every report reads time from.
            on_refusal: Awaited with a ConnectionRefusal each time a check refuses.
        """
        self._ceiling = ceiling
        self._replay = replay
        self._clock = clock
        self._on_refusal = on_refusal
        # Learned from the envelopes this connection delivers: a frame that trips the ceiling has
        # not been decoded, so the node it came from is the one the connection last spoke for.
        self._node_id: str | None = None

    async def frame_admitted(self) -> bool:
        """Count one received frame against this connection's ceiling.

        Returns:
            True when the frame is within the ceiling, or no ceiling is configured. False when
            the connection is over it, after the refusal has been reported: the transport then
            closes with code 4029 and ends its receive.

        Raises:
            TimeoutError: The ``on_refusal`` callback took longer than REFUSAL_REPORT_TIMEOUT_S.
        """
        if self._ceiling is None:
            return True
        now = self._clock.now()
        if self._ceiling.admit(now):
            return True
        await self._report(RefusalKind.FRAME_CEILING, None, now)
        return False

    async def envelope_admitted(self, envelope: Envelope) -> bool:
        """Check one decoded envelope against the shared replay window.

        Args:
            envelope: An envelope the codec has just decoded (and, with a verifier, verified), so
                its ``node_id`` is the node whose key signed it.

        Returns:
            True when the envelope is new, or no replay window is configured. False when its
            ``(node_id, id)`` was already seen inside the window, after the refusal has been
            reported: the transport then drops it without yielding it.

        Raises:
            TimeoutError: The ``on_refusal`` callback took longer than REFUSAL_REPORT_TIMEOUT_S.
        """
        self._node_id = envelope.node_id
        if self._replay is None:
            return True
        now = self._clock.now()
        if not self._replay.seen(envelope.node_id, envelope.id, now):
            return True
        await self._report(RefusalKind.REPLAY, envelope.id, now)
        return False

    async def _report(self, kind: RefusalKind, envelope_id: str | None, at: datetime) -> None:
        """Hand one refusal to the callback, bounded in time; do nothing when there is none."""
        if self._on_refusal is None:
            return
        refusal = ConnectionRefusal(
            node_id=self._node_id, kind=kind, envelope_id=envelope_id, at=at
        )
        # The callback is the listener wrapper's trail write: local and fast, so the bound only
        # catches a recorder that is stuck. On timeout the TimeoutError reaches the receiver
        # unchanged rather than being swallowed: a refusal nobody could record must be loud.
        async with asyncio.timeout(REFUSAL_REPORT_TIMEOUT_S):
            await self._on_refusal(refusal)
