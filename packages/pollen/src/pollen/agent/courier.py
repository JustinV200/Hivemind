"""Send the packet's frames: live ones through the link, durable ones through the outbox first.

Everything the Pollen Packet (the thin gateway program on a device the Hive borrows) says goes
through a ``Courier``. It builds the envelope (the fixed outer record every Waggle message rides
in) from the packet's identity and the injected clock, addressed to the requester or to the Queen
(the Hive's central orchestrator), and sends it on the current link when there is one. Output
chunks are *live*: with no link they are dropped, because a Warden (the per-Cell supervisor) that
lost the link has given up on them. What must reach the Queen eventually (the
``cell.lease_released`` of a release and the terminal ``session.exit`` of an exec that outlived
the link) is *durable*: it is appended to the outbox (flushed to disk) before it is sent,
acknowledged once sent, and replayed after a reconnection (gateway-contract section 14). The
courier owns the outbox and the lock that orders appends and the replay, and it is the single
place a frame is built for the wire, so there is exactly one path by which the packet speaks.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by the composition root;
    attached to each Link by pollen.agent.packet; used by every executor through the Reporter
    Protocol; calls into waggle's envelope, outbox and clock only.

Key invariants:
    - A durable frame is on disk before it is sent, and acknowledged only after it was sent.
    - A frame keeps its id and ``sent_at`` through the outbox, so a replay is a duplicate the
      receiver drops, never a second command.
    - Appends and the replay never run at once: one lock orders every outbox operation.
    - A live frame with no link is dropped, never queued.

See Also:
    - docs/waggle/gateway-contract.md sections 8 and 14 (what is sent, and the outbox).
    - pollen.executors.base for the Reporter Protocol this class satisfies.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pollen.agent.link import Link
from pollen.errors import PollenError
from pollen.executors.base import error_payload, request_id_of
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import TransportError
from waggle.ids import DeviceId, HiveId, MessageId, NodeId
from waggle.messages.base import WaggleMessage
from waggle.outbox import Outbox, ReplayReport, expire_older_than, replay_outbox

log = logging.getLogger(__name__)

__all__ = ["Courier", "Identity"]


@dataclass(frozen=True, slots=True)
class Identity:
    """Who the packet is on the wire."""

    device_id: DeviceId  # The sender address of every frame.
    node_id: NodeId  # The node whose key signs it.
    hive_id: HiveId  # The Queen's address: the recipient of what is not an answer.


class Courier:
    """Builds, signs-by-link and sends the packet's frames.

    Owns mutable state (codingrules 8.5, documented): the link currently attached, and an
    ``asyncio.Lock`` that orders every operation on the outbox (append, ack, replay).
    """

    def __init__(self, identity: Identity, clock: Clock, outbox: Outbox) -> None:
        """Bind the courier to the packet's identity, clock and outbox.

        Args:
            identity: Who the packet is on the wire.
            clock: Stamps ids and ``sent_at``.
            outbox: The durable queue of frames that must reach the Queen.
        """
        self._identity = identity
        self._clock = clock
        self._outbox = outbox
        self._link: Link | None = None
        self._outbox_lock = asyncio.Lock()

    @property
    def is_attached(self) -> bool:
        """Whether a link is attached and still open."""
        return self._link is not None and self._link.is_connected

    @property
    def link(self) -> Link | None:
        """The attached link, or None between connections."""
        return self._link

    @property
    def pending(self) -> int:
        """How many frames wait in the outbox."""
        return len(self._outbox)

    def attach(self, link: Link) -> None:
        """Start sending on ``link``; called once the connection has announced itself."""
        self._link = link

    def detach(self) -> None:
        """Stop sending on the current link (it ended); live frames are then dropped."""
        self._link = None

    def envelope(
        self, payload: WaggleMessage, *, to: str, correlation_id: MessageId | None = None
    ) -> Envelope:
        """Wrap ``payload`` as a frame from this packet.

        Args:
            payload: The message.
            to: The recipient's bee address.
            correlation_id: The request the message answers, if any.

        Returns:
            An unsigned envelope; the link's codec signs it when it is sent.
        """
        hop = Hop(sender=self._identity.device_id, recipient=to, node_id=self._identity.node_id)
        return wrap(payload, hop, clock=self._clock, correlation_id=correlation_id)

    async def send(
        self, payload: WaggleMessage, *, to: str, correlation_id: MessageId | None = None
    ) -> bool:
        """Send now if a link is attached; never queue.

        Args:
            payload: The message.
            to: The recipient's bee address.
            correlation_id: The request the message answers, if any.

        Returns:
            True if the frame was handed to the link; False if there is no link or it dropped.
        """
        link = self._link
        if link is None:
            return False
        try:
            await link.send(self.envelope(payload, to=to, correlation_id=correlation_id))
        except (TransportError, TimeoutError):
            return False  # The link is gone; the reader notices and the loop reconnects.
        return True

    async def send_durable(
        self, payload: WaggleMessage, *, to: str, correlation_id: MessageId | None = None
    ) -> None:
        """Queue in the outbox first, then send if a link is attached.

        Args:
            payload: A message that must reach the Queen eventually.
            to: The recipient's bee address.
            correlation_id: The request the message answers, if any.
        """
        envelope = self.envelope(payload, to=to, correlation_id=correlation_id)
        async with self._outbox_lock:
            # Disk first (fsync under a thread): a crash after this line loses nothing.
            await asyncio.to_thread(self._outbox.append, envelope)
        link = self._link
        if link is None:
            return  # Stays pending; the next reconnection replays it.
        try:
            await link.send(envelope)
        except (TransportError, TimeoutError):
            return  # Sent or not, it is still pending; a duplicate is harmless (same id).
        async with self._outbox_lock:
            await asyncio.to_thread(self._outbox.ack, envelope.id)

    async def fail(self, request: Envelope, error: PollenError) -> None:
        """Answer a request that failed with a ``control.error``.

        Args:
            request: The request that failed; its sender is who is told.
            error: The refusal; its code and retryability are fixed by its class.
        """
        reply = error_payload(error, request.kind)
        answered = await self.send(reply, to=request.sender, correlation_id=request_id_of(request))
        if not answered:
            log.debug("pollen.error_reply_dropped code=%s", error.code)

    async def flush(self, link: Link) -> ReplayReport:
        """Drain the outbox through ``link`` in order, after dropping what is too old.

        Args:
            link: The connection that has just announced itself.

        Returns:
            What the replay did; ``stopped_by`` is set if the link failed part way.
        """
        async with self._outbox_lock:
            await asyncio.to_thread(expire_older_than, self._outbox, self._clock)
            return await replay_outbox(self._outbox, link)
