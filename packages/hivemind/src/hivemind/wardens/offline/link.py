"""Hold a Warden's link to the Queen across outages: ReconnectingQueenLink, a Transport that mends.

A Warden (the always-on supervisor of one Cell, a unit of compute) that dials the Queen (the Hive's
central orchestrator) from another machine, as a colonized device's does, holds this instead of a
bare WebSocket client. It satisfies the same `Transport` protocol, so the Warden changes nothing,
and adds the one thing a bare client leaves to its caller: surviving the loss of the link
(ADR-0053). While the link is up it forwards every send. When the link is lost, the one task that
reads it (the Warden's own receive task, which drives `receive`) reports OFFLINE to whoever
`watch`es, then redials with capped backoff, re-announces the Cell, replays the durable outbox,
ships the trail segment and records `warden.reconnected` (`hivemind.wardens.offline.reconnect`),
and only then reports ONLINE again. `receive` never ends in between, so the Warden's loop simply
waits (the bare client's iterator ends and a Warden that reads it again spins).

What a Warden sends while the link is down is routed by `hivemind.wardens.offline.policy`: a
result, an Alarm, a checkpoint or a request for more Forage is queued durably and goes out in
order after the reconnection; a human-bound question is held in memory and goes out last; a
`cell.request` is refused with a typed error; a heartbeat is dropped; and anything else is refused
too, which is what a dead link does today. A send that finds the link dead mid-flight takes the
same road, so a result handed over a moment after the cable is pulled is queued, not lost.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Built by the composition root (`hivemind.cli.in_cell` in borrowed mode)
    around a dialer and an announcer, and handed to the Warden as `WardenDeps.queen_link`. Calls
    into `hivemind.wardens.offline.outbox`, `.policy` and `.reconnect` and waggle only.

Key invariants:
    - The link recovers only while something drives `receive`; the Warden always does, and a
      test that wants a recovery drives it too. `connect` makes the first connection the same way.
    - `receive` yields every frame of every connection in arrival order and ends only after
      `close`; a frame that is invalid but readable is skipped (the Warden would ignore it too).
    - The online flip happens with the outbox's lock held and the queue empty, and the routing of
      a send happens under that same lock, so no message is queued after the last replay.
    - `close` ends a pending `receive` through the connection's own close, as the WebSocket
      transport's does; a Warden's `stop` cancels its receive task before the link is closed.
    - Sends are serialised: the recovery's frames and the Warden's never interleave on the wire.
    - While recovering, only a trail-segment sync passes through to the new connection, since the
      recovery asks for exactly that; everything else is routed as if the link were still down.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the decision.
    - hivemind.wardens.offline.freeze for what the Warden does with its sub-bees meanwhile.
    - waggle.transport.base for the Transport contract this link keeps.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from enum import Enum

from hivemind.common.logging import get_logger
from hivemind.wardens.offline.outbox import SEND_TIMEOUT_S, OfflineOutbox
from hivemind.wardens.offline.policy import OfflineAction, OfflineRefusedError, decide_offline
from hivemind.wardens.offline.reconnect import (
    RETRYABLE,
    Reconnection,
    close_quietly,
    redial,
    rejoin,
)
from waggle.envelope import Envelope
from waggle.errors import (
    CodecError,
    ConnectionLostError,
    InvalidPayloadError,
    SignatureError,
    TransportClosedError,
)
from waggle.messages.swarm import TrailSegmentSync
from waggle.transport.base import Transport
from waggle.transport.websocket_client import RECONNECT_INITIAL_S

_LOG = get_logger(__name__)
# What a send on a connection that has just died can raise; the message takes the offline road.
_SEND_FAILED = (ConnectionLostError, TransportClosedError, TimeoutError)

__all__ = ["LinkPhase", "ReconnectingQueenLink"]


class LinkPhase(Enum):
    """Where the link is in an outage and its mending."""

    OFFLINE = "OFFLINE"  # No usable connection; sends are routed by the offline policy.
    RECOVERING = "RECOVERING"  # Connected and announced; catching the Queen up. Still routed.
    ONLINE = "ONLINE"  # Forwarding every send.


class ReconnectingQueenLink:
    """A `Transport` to the Queen that redials, replays and reports its own state.

    Owns mutable state (codingrules 8.5, documented): the current connection, the phase and the
    list of watchers change only inside this class, the connection and phase under the outbox's
    lock where a send's routing depends on them.
    """

    def __init__(self, reconnection: Reconnection, outbox: OfflineOutbox) -> None:
        """Build a link that is OFFLINE until `connect`, over a dialer and a durable queue."""
        self._reconnection = reconnection
        self._outbox = outbox
        self._inner: Transport | None = None
        self._phase = LinkPhase.OFFLINE
        self._closed = False
        self._watchers: list[Callable[[], None]] = []
        self._lost_at = reconnection.clock.monotonic()  # When the current outage began.
        # One frame at a time on the wire: the recovery's frames and the Warden's must not mix
        # (the Transport contract: two tasks sending on one connection is the caller's bug).
        self._send_lock = asyncio.Lock()

    @property
    def is_online(self) -> bool:
        """Whether the link is forwarding sends; False while it is lost or being mended."""
        return self._phase is LinkPhase.ONLINE

    @property
    def is_connected(self) -> bool:
        """The Transport's name for `is_online`."""
        return self.is_online

    @property
    def phase(self) -> LinkPhase:
        """Where the link is: OFFLINE, RECOVERING or ONLINE."""
        return self._phase

    def watch(self, on_change: Callable[[], None]) -> None:
        """Call `on_change` on every new phase; it runs inline, so it must be trivial."""
        self._watchers.append(on_change)

    async def connect(self) -> None:
        """Make the first connection (or return at once if online), retrying until it is up."""
        if self._phase is not LinkPhase.ONLINE:
            await self._recover()

    async def send(self, envelope: Envelope) -> None:
        """Forward `envelope`, or route it by the offline policy while the link is down.

        Args:
            envelope: The message to send.

        Raises:
            TransportClosedError: The link was closed.
            OfflineRefusedError: The link is down and the message may not wait for it.
            waggle.errors.FrameTooLargeError: The frame would exceed the codec's limit.
        """
        if self._closed:
            raise TransportClosedError("The reconnecting link to the Queen was closed.")
        if self._phase is LinkPhase.ONLINE:
            await self._send_live(envelope)
        elif self._phase is LinkPhase.RECOVERING and isinstance(envelope.payload, TrailSegmentSync):
            # The recovery asks for exactly this sync, over the connection it has just made.
            await self._send_inner(envelope)
        else:
            await self._route_offline(envelope)

    async def receive(self) -> AsyncGenerator[Envelope, None]:
        """Yield every frame the Queen sends, across reconnections, until the link is closed."""
        while not self._closed:
            inner = self._inner
            if inner is None:
                await self._recover()
                continue
            reason = "the Queen closed the connection"
            try:
                async for envelope in inner.receive():
                    yield envelope
            except InvalidPayloadError:
                # A readable frame with a bad payload leaves the connection open; the Warden
                # ignores such a frame, so skipping it here is the same thing.
                _LOG.debug("wardens.offline.invalid_payload_skipped")
                continue
            except (ConnectionLostError, TransportClosedError, CodecError, SignatureError) as exc:
                reason = type(exc).__name__
            if not self._closed:
                await self._lose(reason)
                await self._recover()

    async def close(self) -> None:
        """Close the connection for good; later sends raise and `receive` ends."""
        if self._closed:
            return
        self._closed = True
        inner, self._inner = self._inner, None
        self._set_phase(LinkPhase.OFFLINE)
        if inner is not None:
            await close_quietly(inner)

    async def _send_live(self, envelope: Envelope) -> None:
        """Send on the live connection; a link found dead takes the road a down link takes."""
        try:
            await self._send_inner(envelope)
        except _SEND_FAILED as exc:
            await self._lose(f"a send found the link gone ({type(exc).__name__})")
            await self._route_offline(envelope)

    async def _send_inner(self, envelope: Envelope) -> None:
        """Put one frame on the current connection, alone on the wire and within a time limit."""
        inner = self._inner
        if inner is None:
            raise ConnectionLostError("The link to the Queen has no connection.")
        async with self._send_lock:
            async with asyncio.timeout(
                SEND_TIMEOUT_S
            ):  # A live link takes a frame in milliseconds.
                await inner.send(envelope)

    async def _route_offline(self, envelope: Envelope) -> None:
        """Queue, hold, refuse or drop `envelope` as the offline policy says."""
        action = decide_offline(envelope.payload)
        if action is OfflineAction.DROP:
            return
        if action is OfflineAction.REFUSE:
            raise OfflineRefusedError(
                f"Message {envelope.id} ({envelope.kind}) may not be sent while this Warden is "
                "cut off from the Queen."
            )
        async with self._outbox.lock:
            # The link may have come back while this send waited for the lock: then it just goes.
            if self._phase is LinkPhase.ONLINE:
                try:
                    await self._send_inner(envelope)
                    return
                except _SEND_FAILED as exc:
                    # Lost again in the instant it was back; the lock is still held, so keep it.
                    await self._lose(f"a send found the link gone ({type(exc).__name__})")
            if action is OfflineAction.HOLD:
                self._outbox.hold(envelope)
            else:
                await self._outbox.queue(envelope)

    async def _lose(self, reason: str) -> None:
        """Mark the link lost: OFFLINE, watchers told, the dead connection closed."""
        if self._phase is LinkPhase.OFFLINE:
            return
        inner, self._inner = self._inner, None
        self._lost_at = self._reconnection.clock.monotonic()
        _LOG.warning("wardens.offline.link_lost", reason=reason)
        self._set_phase(LinkPhase.OFFLINE)
        if inner is not None:
            # Closing also wakes the receive loop, if it is the one still blocked on this link.
            await close_quietly(inner)

    async def _recover(self) -> None:
        """Dial, catch the Queen up and go online, as often as it takes."""
        clock = self._reconnection.clock
        while not self._closed:
            transport = await redial(self._reconnection)
            self._inner = transport
            self._set_phase(LinkPhase.RECOVERING)
            outage_s = max(0.0, clock.monotonic() - self._lost_at)
            try:
                await rejoin(self._reconnection, self._outbox, transport, self._go_online, outage_s)
            except RETRYABLE as exc:
                # The new link died while the Queen was being caught up: nothing unsent was lost,
                # so mend it again from the dial.
                _LOG.warning("wardens.offline.rejoin_failed", error=type(exc).__name__)
                self._inner = None
                self._set_phase(LinkPhase.OFFLINE)
                await close_quietly(transport)
                # A link that opens and dies at once must not be redialled in a hot loop.
                await clock.sleep(RECONNECT_INITIAL_S)
                continue
            return

    def _go_online(self) -> None:
        """Declare the link online; called by `rejoin` with the outbox's lock held."""
        _LOG.info("wardens.offline.link_back")
        self._set_phase(LinkPhase.ONLINE)

    def _set_phase(self, phase: LinkPhase) -> None:
        """Change the phase and tell the watchers, once per real change."""
        if phase is self._phase:
            return
        self._phase = phase
        for notify in self._watchers:
            notify()
