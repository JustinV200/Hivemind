"""Wrap a Transport so what it sends is remembered, and can be sent again: RememberingTransport.

Waggle (the Hive's bee-to-bee wire protocol, named after the honeybee waggle dance) leaves it to the
sender to get a frame through: a ``send`` that returned has handed the frame to a connection that
may already be dead (``waggle.transport.recent.ring`` says how). A node that must not lose a frame
puts this wrapper around the connection it just made. Every frame the connection takes, and that the
node's ``wanted`` test says must arrive, goes into a ``RecentFrames`` once its ``send`` has
returned; when a later connection is made, ``resend`` sends the frames that may never have arrived
again, oldest first, under their own envelope ids, so the receiver's replay window drops what it
already had. The wrapper is the Transport itself as far as its caller can tell: the Warden's
``ReconnectingQueenLink`` and the Pollen Packet's courier each hand it to the outbox replay too, so
a frame replayed from the outbox is remembered the same way, and a replay that went into a dead
link is not lost a second time.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Built by the Warden's link
    (``hivemind.wardens.offline.link``) around each connection it makes and by the packet's courier
    (``pollen.agent.courier``) around each link it replays through; calls into
    waggle.transport.recent.ring, waggle.transport.base and waggle.clock only.

Key invariants:
    - A frame is remembered only after the inner ``send`` returned: a send that raised put nothing
      on the connection and is the caller's to queue, so it is never remembered.
    - Only frames the ``wanted`` test accepts are remembered; the rest pass through untouched.
    - ``resend`` sends in the order the frames were last sent, each under its original id and
      ``sent_at``, and once the whole batch is out each frame is remembered afresh, in that order:
      it is as recent as this send, and should this connection die unnoticed too it will be sent
      again. The caller sends nothing else on the connection while it runs.
    - Every ``send`` ``resend`` makes has a timeout; a failure ends the re-send and leaves every
      frame held, with the stamps and the order it had, so the next connection makes it again from
      the start.

See Also:
    - waggle.transport.recent.ring for the bounded memory this fills and reads.
    - docs/waggle/gateway-contract.md section 14 for what a gateway sends again, and when.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.messages.base import WaggleMessage
from waggle.transport.base import Transport
from waggle.transport.recent.ring import RecentFrames

RESEND_TIMEOUT_S = 30.0  # One frame to a connection that has just been made; longer is a dead link.

__all__ = ["RESEND_TIMEOUT_S", "RememberingTransport", "Wanted", "every_frame"]

# Says whether a message must arrive, so a frame carrying it is worth sending again.
type Wanted = Callable[[WaggleMessage], bool]


def every_frame(_message: WaggleMessage) -> bool:
    """Accept every message: for a sender whose own path already holds only what must arrive."""
    return True


class RememberingTransport:
    """A Transport that keeps what it sends in a RecentFrames, and can send it again.

    Satisfies ``waggle.transport.base.Transport`` structurally: everything but ``send`` is the
    inner transport's own. Concurrency model: the same as the inner transport's, one task sending
    at a time; the ring it fills is touched only from that task and from ``resend``.
    """

    def __init__(
        self, inner: Transport, recent: RecentFrames, clock: Clock, wanted: Wanted = every_frame
    ) -> None:
        """Wrap `inner`, remembering into `recent`.

        Args:
            inner: The connection the node sends on.
            recent: The memory to fill; it outlives this connection and is shared by the next one.
            clock: Its ``monotonic`` stamps each send, so the ring ages frames in the node's time.
            wanted: Says which messages must arrive; a frame carrying any other is not remembered.
        """
        self._inner = inner
        self._recent = recent
        self._clock = clock
        self._wanted = wanted

    @property
    def is_connected(self) -> bool:
        """Whether the inner transport can still carry frames."""
        return self._inner.is_connected

    async def connect(self) -> None:
        """Establish the inner connection, or return at once when it already is."""
        await self._inner.connect()

    async def send(self, envelope: Envelope) -> None:
        """Send `envelope` on the inner transport and, if it must arrive, remember it.

        Args:
            envelope: The message to send.

        Raises:
            waggle.errors.TransportError: The inner send failed; nothing was remembered.
        """
        await self._inner.send(envelope)
        # Only now: a send that raised put nothing on the connection, so it is not "sent".
        if self._wanted(envelope.payload):
            self._recent.remember(envelope, self._clock.monotonic())

    def receive(self) -> AsyncIterator[Envelope]:
        """Yield what the inner transport receives, unchanged."""
        return self._inner.receive()

    async def close(self) -> None:
        """Close the inner connection; a second call does nothing."""
        await self._inner.close()

    async def resend(self, lost_at: float) -> int:
        """Send again the frames that may never have arrived on the connection that was lost.

        Args:
            lost_at: When the node found that connection dead (its Clock's ``monotonic``).

        Returns:
            How many frames were sent again.

        Raises:
            waggle.errors.TransportError: The new connection failed part way; every frame is
                still held, and the caller makes the re-send again on the next connection.
            TimeoutError: A frame was not taken within RESEND_TIMEOUT_S.
        """
        frames = self._recent.due(lost_at, self._clock.monotonic())
        for envelope in frames:
            async with asyncio.timeout(RESEND_TIMEOUT_S):  # One frame; longer is a dead link.
                await self._inner.send(envelope)
        # Only once the whole batch is out, and in its own order: remembering a frame moves it to
        # the young end, so doing it frame by frame would leave a failed re-send's first frames
        # behind the rest, and the next connection would send them out of order.
        sent_at = self._clock.monotonic()
        for envelope in frames:
            self._recent.remember(envelope, sent_at)
        return len(frames)
