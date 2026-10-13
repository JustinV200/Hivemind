"""Define DeviceLink: one connected Pollen Packet, spoken to as Queen or as the device's Warden.

When an enrolled device's packet connects out to the swarm listener (and its first frame
verifies), the connection becomes a `DeviceLink`: the Hive's one handle on that device. It owns
the connection's single reader task, which routes every frame the device sends by its
`correlation_id` into the `Exchange` waiting for it, and hands every frame nobody is waiting for
(the periodic `swarm.device_heartbeat`, a `forage.capacity_report`, a dead-man switch's
unprompted `cell.lease_released`) to one `on_frame` callback. Callers speak through it: `request`
sends one request and returns its answer, `stream` yields everything a request draws up to the
terminal frame, `send` sends an event, and `exchange` opens a conversation by hand for the cases
that need more (a chunked upload whose answer correlates to the first chunk). Every send is
wrapped with a sender address the caller names, because two bees share the link: the Queen
(`hive_id`) sends the Queen-only kinds and lease requests, and the device's Warden (`warden_id`)
sends session traffic. The Hive Stand's own node id and key sign every frame.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Built by the connection router after a device's first frame verifies; held by the gateway
    hub; used by `SwarmSource`, `PollenSession`, the watch probe and the Queen's swarm gate
    (Layer 6). Calls into waggle (envelope, ids, messages) and the rest of
    hivemind.swarm.gateway only.

Key invariants:
    - Exactly one task reads the connection, and the sender serialises its sends, because a
      `Transport` allows one sender and one receiver at a time.
    - Every frame is routed to at most one exchange, by the correlation id it carries; a frame
      that matches none is passed to `on_frame` exactly once.
    - When the reader ends, for any reason, every open exchange is told, so no caller waits on a
      link that is gone; `wait_closed` then returns the reason.
    - A send on a closed link raises `GatewayUnavailableError`; a send the link cannot take in
      `SEND_TIMEOUT_S` raises `DeviceTimeoutError`.

See Also:
    - docs/waggle/gateway-contract.md sections 5 and 6 for the link and the sender rules.
    - hivemind.swarm.gateway.exchange for the conversations frames are routed into.
    - hivemind.swarm.gateway.hub for how links are found by device and by Cell.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from hivemind.common.logging import get_logger
from hivemind.swarm.errors import GatewayUnavailableError
from hivemind.swarm.gateway.exchange import Exchange, RouteTable
from hivemind.swarm.gateway.frames import ENDED_BY, Connection, FrameSender, LinkEnds
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.ids import DeviceId, MessageId, new_message_id
from waggle.messages.base import WaggleMessage

log = get_logger(__name__)

__all__ = ["DeviceLink", "FrameHandler", "LinkEnds"]

FrameHandler = Callable[[Envelope], Awaitable[None]]


class DeviceLink:
    """One connected packet: send to it, wait for its answers, hear what it says unprompted."""

    def __init__(
        self, connection: Connection, ends: LinkEnds, clock: Clock, on_frame: FrameHandler
    ) -> None:
        """Wrap a connection whose first frame has verified; call `start` to begin reading.

        Args:
            connection: The accepted connection and its single frame iterator.
            ends: The Hive's address and the device's.
            clock: Mints envelope ids and times, and the monotonic reading `last_heard` keeps.
            on_frame: Awaited with every frame no exchange is waiting for. It must be quick: the
                reader waits for it before it reads the next frame.
        """
        self._connection = connection
        self._ends = ends
        self._clock = clock
        self._on_frame = on_frame
        self._sender = FrameSender(connection.transport, ends, clock)
        self._routes = RouteTable()
        self._reader: asyncio.Task[None] | None = None
        self._open = True
        self._reason = "open"
        self._ended = asyncio.Event()
        self._last_heard = clock.monotonic()

    @property
    def device_id(self) -> DeviceId:
        """The device this link reaches."""
        return self._ends.device_id

    @property
    def hive_id(self) -> str:
        """The Queen's bee address: the sender of Queen-kind frames and lease requests."""
        return str(self._ends.voice.hive_id)

    @property
    def is_open(self) -> bool:
        """Whether the link can still send and its reader is running."""
        return self._open and self._connection.transport.is_connected

    @property
    def last_heard(self) -> float:
        """The monotonic reading (`Clock.monotonic`) when a frame last arrived on this link."""
        return self._last_heard

    @property
    def routes(self) -> RouteTable:
        """The table that routes this link's incoming frames to the exchanges waiting for them."""
        return self._routes

    def start(self) -> None:
        """Start the reader task: from now on frames are routed and the link can be answered."""
        self._reader = asyncio.ensure_future(self._read())

    async def wait_closed(self) -> str:
        """Wait until the link has ended and return why (a stable code, or "closed")."""
        await self._ended.wait()
        return self._reason

    def mint_id(self) -> MessageId:
        """Mint a fresh envelope id for a frame this link is about to send."""
        return new_message_id(self._clock)

    def exchange(self, message_id: MessageId | None = None) -> Exchange:
        """Open a conversation: frames correlated to its ids are routed into it.

        Args:
            message_id: The id of its first frame; minted when omitted (a put-file passes the
                transfer id already in its payload).

        Returns:
            The registered exchange; use it as `async with link.exchange() as x`.

        Raises:
            GatewayUnavailableError: The link has ended.
        """
        self._require_open()
        return Exchange(self, message_id or self.mint_id())

    async def request(self, payload: WaggleMessage, *, sender: str, timeout_s: float) -> Envelope:
        """Send one request and return the frame that answers it.

        Args:
            payload: The request.
            sender: The bee address it comes from: `hive_id` or the Warden's `warden_id`.
            timeout_s: Real seconds to wait for each frame of the answer.

        Returns:
            The first answering frame that is not a `session.output` chunk.

        Raises:
            DeviceRequestError: The device answered with a `control.error`.
            DeviceTimeoutError: The device did not answer in time.
            GatewayUnavailableError: The link is gone, or ended before the answer.
        """
        async with self.exchange() as exchange:
            await exchange.send(payload, sender=sender)
            return await exchange.reply(timeout_s)

    async def stream(
        self, payload: WaggleMessage, *, sender: str, timeout_s: float
    ) -> AsyncIterator[Envelope]:
        """Send one request and yield every frame it draws, the terminal one last.

        Args:
            payload: The request.
            sender: The bee address it comes from.
            timeout_s: Real seconds to wait for each next frame (an idle bound).

        Yields:
            Each correlated frame in order, the first that is not an output chunk being the last.

        Raises:
            DeviceRequestError: The device answered with a `control.error`.
            DeviceTimeoutError: The device went silent for `timeout_s`.
            GatewayUnavailableError: The link is gone, or ended before the terminal frame.
        """
        async with self.exchange() as exchange:
            await exchange.send(payload, sender=sender)
            async for envelope in exchange.events(timeout_s):
                yield envelope

    async def send(
        self, payload: WaggleMessage, *, sender: str, correlation_id: MessageId | None = None
    ) -> MessageId:
        """Send one event, expecting no answer.

        Args:
            payload: The event.
            sender: The bee address it comes from.
            correlation_id: What it follows from, when it has an antecedent.

        Returns:
            The envelope id of the frame sent.

        Raises:
            GatewayUnavailableError: The link is gone.
            DeviceTimeoutError: The link could not take the frame in time.
        """
        return await self.transmit(
            payload, sender=sender, correlation_id=correlation_id, message_id=self.mint_id()
        )

    async def transmit(
        self,
        payload: WaggleMessage,
        *,
        sender: str,
        correlation_id: MessageId | None,
        message_id: MessageId,
    ) -> MessageId:
        """Send a frame under an id the caller chose (an exchange's first uses its own)."""
        self._require_open()
        return await self._sender.send(
            payload, sender=sender, correlation_id=correlation_id, message_id=message_id
        )

    async def close(self) -> None:
        """Close the connection and stop the reader; idempotent."""
        self._open = False
        await self._connection.transport.close()
        await _stop_reader(self._reader, self.device_id)
        self._finish("closed")

    async def _read(self) -> None:
        """Route every frame the connection yields until it ends."""
        reason = "closed"
        try:
            async for envelope in self._connection.frames:
                await self._route(envelope)
        except ENDED_BY as exc:
            reason = exc.code
        finally:
            self._finish(reason)

    async def _route(self, envelope: Envelope) -> None:
        """Deliver a frame to the exchange waiting for it, or to `on_frame` when none is."""
        self._last_heard = self._clock.monotonic()
        exchange = self._routes.target(envelope)
        if exchange is not None:
            await exchange.deliver(envelope)
        else:
            await self._on_frame(envelope)

    def _finish(self, reason: str) -> None:
        """Mark the link ended exactly once and tell every open exchange."""
        if self._ended.is_set():
            return
        self._open = False
        self._reason = reason
        self._routes.fail_all(reason)
        self._ended.set()

    def _require_open(self) -> None:
        """Raise `GatewayUnavailableError` unless the link can still be used."""
        if not self.is_open:
            raise GatewayUnavailableError(f"Link to {self.device_id} is closed ({self._reason}).")


async def _stop_reader(reader: asyncio.Task[None] | None, device_id: DeviceId) -> None:
    """Cancel a link's reader task and wait for it, logging a reader that died of a bug."""
    if reader is None:
        return
    reader.cancel()
    # gather, not a bare await: it collects the cancelled reader without swallowing a
    # cancellation of the task that called close().
    outcome = await asyncio.gather(reader, return_exceptions=True)
    failure = outcome[0]
    if isinstance(failure, Exception):
        # The type only: a pydantic error's repr would carry the frame that failed, and a frame
        # is the device's content.
        log.error("swarm.link_reader_failed", device_id=device_id, error=type(failure).__name__)
