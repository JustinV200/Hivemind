"""Define Exchange: one request and everything the device answers it with, on a shared link.

A Pollen Packet answers a request with frames correlated to it by `correlation_id`: a lease
request draws one `cell.lease_opened`; an exec draws a run of `session.output` chunks and then
one `session.exit`; a file upload is a run of chunk events and draws one `session.exit` correlated
to the first chunk's id; any request may instead draw a `control.error`. Many such conversations
share one link, so each is an `Exchange`: a small inbox the link's single reader routes frames
into by correlation id, and the caller waits on. An exchange knows every envelope id it has sent
(a put-file's chunks each count, because a device may answer a failing chunk, not only the
transfer) and registers them all with its link, so a reply to any of them lands in the same
place. A `control.error` is raised to the waiting caller as `DeviceRequestError`; a link that
ends under it raises `GatewayUnavailableError`; a device that stays silent raises
`DeviceTimeoutError`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Opened by `DeviceLink.exchange` (and through it by `request` and `stream`) and used by
    `SwarmSource`, `PollenSession` and the watch probe. Calls into hivemind.swarm.errors and
    waggle only.

Key invariants:
    - An exchange's inbox is bounded (`EXCHANGE_QUEUE_SIZE`): a device that outruns its reader
      is backed up by the link, never buffered without limit.
    - Closing an exchange deregisters every id it registered and drains its inbox, so the link's
      reader can never block delivering to a caller that has gone away.
    - Output chunks (`session.output`) continue an exchange; any other frame ends it, so the
      terminal frame is always the last one a caller sees.

See Also:
    - docs/waggle/spec.md section 3 for the session rule and the correlation shapes.
    - hivemind.swarm.gateway.link for DeviceLink, which routes frames into exchanges.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Protocol, Self

from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError, GatewayUnavailableError
from waggle.envelope import Envelope
from waggle.ids import DeviceId, MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.session import SessionOutput

EXCHANGE_QUEUE_SIZE = 64  # Frames held for one caller: 64 chunks of 256 KiB is 16 MiB at most.

__all__ = ["EXCHANGE_QUEUE_SIZE", "Exchange", "ExchangeHost", "RouteTable"]


class ExchangeHost(Protocol):
    """What an exchange needs from the link it lives on."""

    @property
    def device_id(self) -> DeviceId:
        """The device this link reaches, for error messages."""
        ...

    @property
    def routes(self) -> RouteTable:
        """The table that routes the link's incoming frames to exchanges."""
        ...

    def mint_id(self) -> MessageId:
        """Mint a fresh envelope id."""
        ...

    async def transmit(
        self,
        payload: WaggleMessage,
        *,
        sender: str,
        correlation_id: MessageId | None,
        message_id: MessageId,
    ) -> MessageId:
        """Wrap `payload` with the Hive's address, sign it and send it; return its id."""
        ...


class RouteTable:
    """Which exchange each correlation id belongs to: where a link's reader looks a frame up.

    Concurrency model: touched only from tasks on one event loop, never across an await.
    """

    def __init__(self) -> None:
        """Create an empty table."""
        self._routes: dict[MessageId, Exchange] = {}

    def register(self, key: MessageId, exchange: Exchange) -> None:
        """Route frames correlated to `key` into `exchange`."""
        self._routes[key] = exchange

    def release(self, exchange: Exchange) -> None:
        """Stop routing to `exchange`: every id it registered."""
        for key in exchange.keys:
            if self._routes.get(key) is exchange:
                del self._routes[key]

    def target(self, envelope: Envelope) -> Exchange | None:
        """Return the exchange a frame is correlated to, or None when nobody is waiting for it."""
        key = envelope.correlation_id
        return None if key is None else self._routes.get(key)

    def fail_all(self, reason: str) -> None:
        """Tell every open exchange that the link ended, and why."""
        for exchange in set(self._routes.values()):
            exchange.fail(reason)


class Exchange:
    """One conversation with a device: send into it, then wait for its replies.

    Concurrency model: one task sends and waits; the link's reader task is the only caller of
    `deliver` and `fail`.
    """

    def __init__(self, host: ExchangeHost, message_id: MessageId) -> None:
        """Open an exchange on `host`, registering its first id so no reply can be missed.

        Args:
            host: The link this conversation shares.
            message_id: The envelope id of the first send; it is also the id replies correlate to
                and the transfer id of a put-file.
        """
        self.id = message_id
        self._host = host
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=EXCHANGE_QUEUE_SIZE)
        self._keys: set[MessageId] = {message_id}
        self._sent_first = False
        self._failure: ErrorMessage | None = None
        self._ended: str | None = None  # Why the link ended under this exchange, once it has.
        self._closed = False
        # Set by the reader after every delivery and when the link ends, so a waiter never polls.
        self._arrived = asyncio.Event()
        host.routes.register(message_id, self)

    async def __aenter__(self) -> Self:
        """Enter the exchange; it is already registered, so this only returns it."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the exchange when the caller is done with it, however it ended."""
        self.close()

    async def send(
        self, payload: WaggleMessage, *, sender: str, correlation_id: MessageId | None = None
    ) -> MessageId:
        """Send `payload` into this conversation; the first send carries the exchange's own id.

        Args:
            payload: The message to send.
            sender: The bee address the frame comes from: the Queen's `hive_id` for Queen kinds,
                the Warden's `warden_id` for Warden traffic.
            correlation_id: What the frame follows from, for an event that has an antecedent.

        Returns:
            The envelope id of the frame sent.

        Raises:
            GatewayUnavailableError: The link is gone.
            DeviceTimeoutError: The link could not take the frame in time.
        """
        message_id = self._host.mint_id() if self._sent_first else self.id
        self._sent_first = True
        # Registered before it is sent, so a device that answers at once is not missed.
        if message_id not in self._keys:
            self._keys.add(message_id)
            self._host.routes.register(message_id, self)
        return await self._host.transmit(
            payload, sender=sender, correlation_id=correlation_id, message_id=message_id
        )

    def raise_if_failed(self) -> None:
        """Raise now if the device has already refused this conversation or the link has ended.

        Used between the chunks of an upload, so a refusal at chunk one stops the rest being
        sent.

        Raises:
            DeviceRequestError: The device answered with a `control.error`.
            GatewayUnavailableError: The link ended.
        """
        if self._failure is not None:
            raise _refusal(self._failure)
        if self._ended is not None:
            raise self._link_lost()

    async def reply(self, timeout_s: float) -> Envelope:
        """Wait for the frame that ends this conversation, skipping output chunks before it.

        Args:
            timeout_s: Real seconds to wait for each frame.

        Returns:
            The first frame that is not a `session.output` chunk: the reply or terminal event.

        Raises:
            DeviceRequestError: The device answered with a `control.error`.
            DeviceTimeoutError: Nothing arrived within `timeout_s`.
            GatewayUnavailableError: The link ended.
        """
        while True:
            envelope = await self._next(timeout_s)
            if not isinstance(envelope.payload, SessionOutput):
                return envelope

    async def events(self, timeout_s: float) -> AsyncIterator[Envelope]:
        """Yield every frame of this conversation, ending with the terminal one.

        Args:
            timeout_s: Real seconds to wait for each next frame (an idle bound, not a total).

        Yields:
            Each `session.output` chunk, then the terminal frame (anything else).

        Raises:
            DeviceRequestError: The device answered with a `control.error`.
            DeviceTimeoutError: Nothing arrived within `timeout_s`.
            GatewayUnavailableError: The link ended.
        """
        while True:
            envelope = await self._next(timeout_s)
            yield envelope
            if not isinstance(envelope.payload, SessionOutput):
                return

    def close(self) -> None:
        """Deregister every id this exchange registered and drop what is left in its inbox."""
        if self._closed:
            return
        self._closed = True
        self._host.routes.release(self)
        # Draining wakes a reader blocked delivering into a full inbox, so it can move on.
        while not self._queue.empty():
            self._queue.get_nowait()

    @property
    def keys(self) -> frozenset[MessageId]:
        """Every envelope id registered for this exchange."""
        return frozenset(self._keys)

    async def deliver(self, envelope: Envelope) -> None:
        """Put a correlated frame in the inbox; called by the link's reader only."""
        if isinstance(envelope.payload, ErrorMessage):
            self._failure = envelope.payload
        if not self._closed:
            await self._queue.put(envelope)
            self._arrived.set()

    def fail(self, reason: str) -> None:
        """Record that the link ended under this exchange; called by the link's reader only."""
        self._ended = reason
        self._arrived.set()

    async def _next(self, timeout_s: float) -> Envelope:
        """Take the next frame, turning an error frame, a timeout or a lost link into raises."""
        try:
            # In-process wait for the reader to deliver; the bound is the device's silence.
            async with asyncio.timeout(timeout_s):
                envelope = await self._get()
        except TimeoutError as exc:
            raise DeviceTimeoutError(
                f"Device {self._host.device_id} did not answer request {self.id} within "
                f"{timeout_s} seconds."
            ) from exc
        if isinstance(envelope.payload, ErrorMessage):
            raise _refusal(envelope.payload)
        return envelope

    async def _get(self) -> Envelope:
        """Take one frame from the inbox, waiting for the reader to deliver or the link to end."""
        while True:
            if not self._queue.empty():
                return self._queue.get_nowait()
            if self._ended is not None:
                raise self._link_lost()
            # Nothing queued and the link still up: sleep until the reader delivers or fails.
            # Clearing first is safe, because no await separates the checks above from it.
            self._arrived.clear()
            await self._arrived.wait()

    def _link_lost(self) -> GatewayUnavailableError:
        """Build the error for a link that ended while a caller waited on it."""
        return GatewayUnavailableError(
            f"The link to device {self._host.device_id} ended ({self._ended}) before request "
            f"{self.id} was answered."
        )


def _refusal(error: ErrorMessage) -> DeviceRequestError:
    """Build the error a device's `control.error` becomes."""
    return DeviceRequestError(error.code, error.message, is_retryable=error.is_retryable)
