"""Provide ConnectionRouter: decide what a connection is from its first frame, and serve it.

The swarm listener hands every accepted connection to the router, which knows nothing about the
connection yet except that it is on the Hive Stand's port. What it is, is decided by the first
frame the peer sends (docs/waggle/enrolment.md sections 5 and 8), looked up by the type of the
frame's payload in one table. A `swarm.enrol_request` is a new device presenting an invite: the
router passes it to the enrolment door, answers with the correlated `swarm.enrol_accept` or a
`control.error` `hive.swarm.enrol_denied`, trusts the new key from then on, and, if the device
carries straight on with a heartbeat, serves it as a link; an address that has made more attempts
in a minute than `EnrolmentAttempts` allows is closed without the door, a reply or a trail row. A
`swarm.device_heartbeat` is an enrolled device's Pollen Packet connecting: the router checks the
node it names, builds a `DeviceLink`, attaches it to the hub (replacing the device's older link,
which it closes), brings the node ONLINE, tells the hub's listeners, sends the device its current
terms (`swarm.node_update`) and then waits for the link to end. A `cell.ready` is the Warden that
lives on a colonized device connecting; the router checks it speaks for the node it names and
hands the connection to the Queen's swarm gate through a `WardenConnectionHandler`. Anything else,
from anyone, closes the connection with no reply: nothing is trusted yet, so nothing is said.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Driven by the swarm listener, one task per accepted connection; calls the enrolment door, the
    registry, the heartbeat monitor and the hub of this package, and waggle for frames. The Queen's
    swarm gate (Layer 6) implements `WardenConnectionHandler`.

Key invariants:
    - A connection is served by exactly one reader: the iterator of its `Connection`. Whatever
      takes the connection over (a link, a Warden handler) reads that same iterator.
    - A connection is pinned to the node of its first verified frame: a frame signed by another
      node ends it. Nothing is sent to a peer whose first frame did not verify, and an enrolment
      denial names ids and a reason code, never the token.
    - `serve` always closes its transport before it returns, and never raises for what a peer
      did: a refused, silent or failing connection is simply closed.
    - A device reconnecting replaces its previous link; the old link's end takes nothing offline.
    - An unauthenticated peer costs the Hive a bounded amount: past its address's enrolment
      attempts a minute, nothing is read, written or sent for it.

See Also:
    - hivemind.swarm.gateway.link for the link a device's connection becomes.
    - hivemind.swarm.gateway.heartbeat for the status moves a link makes.
    - hivemind.queen.cell_gate.listener for the same first-frame decision for Virtual Cells.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from hivemind.common.logging import get_logger
from hivemind.swarm.enrolment import EnrolmentDoor, node_update_for
from hivemind.swarm.errors import (
    DeviceTimeoutError,
    EnrolmentDeniedError,
    GatewayUnavailableError,
    NodeRevokedError,
    SwarmError,
)
from hivemind.swarm.gateway.frames import (
    Connection,
    HiveVoice,
    next_frame,
    open_connection,
    pinned,
)
from hivemind.swarm.gateway.heartbeat import HeartbeatMonitor
from hivemind.swarm.gateway.hub import GatewayHub
from hivemind.swarm.gateway.link import DeviceLink, FrameHandler, LinkEnds
from hivemind.swarm.gateway.refusals import EnrolmentAttempts
from hivemind.swarm.gateway.verifier import ENROL_REQUEST_KIND, KeyRing
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.registry import SwarmRegistry
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.status import CellReady
from waggle.messages.control import ErrorMessage
from waggle.messages.control.protocol import MAX_ERROR_MESSAGE_CHARS
from waggle.messages.swarm import DeviceHeartbeat, EnrolRequest
from waggle.transport.base import Transport

# A peer that connects and says nothing is dropped after this long: an unauthenticated connection
# may not hold a task for free. It matches the handshake bound the listener gives a dial.
FIRST_FRAME_TIMEOUT_S = 10.0
REPLY_TIMEOUT_S = 10.0  # One small reply frame; a peer that cannot take it in this long is gone.
ONLINE_TERMS_REASON = "connected"  # Why the terms a device is sent when it connects are sent.

log = get_logger(__name__)

__all__ = [
    "FIRST_FRAME_TIMEOUT_S",
    "ConnectionRouter",
    "RouterDeps",
    "WardenConnection",
    "WardenConnectionHandler",
]

Handler = Callable[[Connection, Envelope], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RouterDeps:
    """Every collaborator the router works with (codingrules 5.1).

    Attributes:
        registry: Where nodes are read to decide whether a connection may be served.
        door: Admits a new device, atomically spending its invite.
        hub: Where a device's link is attached and found.
        monitor: Records a link coming and going, and applies the frames nobody awaited.
        ring: The keys the listener trusts; a newly enrolled device's key is added to it.
        voice: The Hive Stand's own address, the sender of every reply.
        clock: Mints reply ids and times, and the readings a link's silence is judged by.
    """

    registry: SwarmRegistry
    door: EnrolmentDoor
    hub: GatewayHub
    monitor: HeartbeatMonitor
    ring: KeyRing
    voice: HiveVoice
    clock: Clock


@dataclass(frozen=True, slots=True)
class WardenConnection:
    """A device Warden's connection, handed over once its `cell.ready` has verified.

    Attributes:
        node: The enrolled device the Warden runs on, as the registry held it.
        transport: The accepted transport: send on it, and close it only through `serve`'s end.
        first: The verified `cell.ready` that identified the connection.
        frames: Every frame after it, pinned to the node: the only reader of the transport. The
            handler must not call `transport.receive()`.
    """

    node: SwarmNode
    transport: Transport
    first: Envelope
    frames: AsyncGenerator[Envelope, None]


class WardenConnectionHandler(Protocol):
    """Take over a verified device Warden's connection for as long as it lives."""

    async def handle(self, connection: WardenConnection) -> None:
        """Serve the Warden's connection; the router closes the transport when this returns.

        Args:
            connection: The verified connection and the one iterator of its frames.
        """
        ...


class ConnectionRouter:
    """Serve each accepted connection according to what its first frame says it is."""

    def __init__(self, deps: RouterDeps) -> None:
        """Build the router; bind a Warden handler to accept colonized devices' Wardens.

        Args:
            deps: Every collaborator it works with.
        """
        self._deps = deps
        self._warden_handler: WardenConnectionHandler | None = None
        # The router's own, not a collaborator: it is the one thing that meets unauthenticated
        # peers, and a bucket per listener is exactly the bound wanted.
        self._attempts = EnrolmentAttempts(deps.clock)
        # Dispatch by payload type, never by a kind string, so a first frame of any other type
        # has no entry and the connection is closed.
        self._by_payload: dict[type[WaggleMessage], Handler] = {
            EnrolRequest: self._enrol,
            DeviceHeartbeat: self._device,
            CellReady: self._warden,
        }

    def bind_warden_handler(self, handler: WardenConnectionHandler | None) -> None:
        """Hand every verified device Warden connection to `handler` from now on.

        Args:
            handler: The Queen's swarm gate; None again leaves such connections closed.
        """
        self._warden_handler = handler

    async def serve(self, transport: Transport, remote_address: str | None = None) -> None:
        """Serve one accepted connection until it ends, then close it.

        Args:
            transport: The accepted transport; closed before this returns, whatever happened.
            remote_address: The peer's address as the listener's transport reported it, which
                bounds its enrolment attempts; None when the transport names none.
        """
        deps = self._deps
        connection = open_connection(transport, deps.voice, deps.clock, remote_address)
        try:
            first = await next_frame(connection, FIRST_FRAME_TIMEOUT_S)
            handler = None if first is None else self._by_payload.get(type(first.payload))
            if first is not None and handler is not None:
                await handler(connection, first)
        except (SwarmError, sqlite3.Error, ValidationError) as exc:
            # A registry or trail failure under one connection must not take the listener down;
            # only the error's type is logged, never anything the peer sent.
            log.warning("swarm.connection_failed", error=type(exc).__name__)
        finally:
            await transport.close()

    async def _enrol(self, connection: Connection, first: Envelope) -> None:
        """Admit a new device, answer it, and serve it if it carries straight on."""
        deps = self._deps
        if not self._attempts.allow(connection.remote_address):
            # Over its address's attempts a minute: closed as any untrusted frame is, before the
            # registry is read or a refusal recorded, so a loop of invites costs nothing more.
            log.debug("swarm.enrol_attempts_exceeded", remote_address=connection.remote_address)
            return
        try:
            accept = await deps.door.enrol(first)
        except EnrolmentDeniedError as denied:
            # After a denial no key is pinned, so the connection is closed once the packet has
            # the reason (it never sends a second request: a failed enrolment is a new invite).
            await self._reply(connection, first, _denial(denied))
            return
        node = await deps.registry.node(accept.device_id)
        if node is None:  # The door just created it; a registry that lost it has failed.
            return
        deps.ring.add(node)  # Trusted from here on: its next frame verifies against this key.
        if not await self._reply(connection, first, accept):
            return
        following = pinned(connection, first.node_id)
        second = await next_frame(following, FIRST_FRAME_TIMEOUT_S)
        if second is not None and isinstance(second.payload, DeviceHeartbeat):
            await self._device(following, second)

    async def _device(self, connection: Connection, first: Envelope) -> None:
        """Serve an enrolled device's packet: attach its link and live until it ends."""
        deps = self._deps
        node = await deps.registry.node_by_node_id(first.node_id)
        if node is None or not _is_this_device(node, first):
            return
        try:
            current = await deps.monitor.admit(node)
        except NodeRevokedError:
            return
        link = DeviceLink(
            pinned(connection, node.node_id),
            LinkEnds(deps.voice, node.device_id),
            deps.clock,
            self._frame_handler(node),
        )
        await self._serve_link(current, link, first)

    async def _serve_link(self, node: SwarmNode, link: DeviceLink, first: Envelope) -> None:
        """Attach a verified link, announce the device and wait for the link to end."""
        deps = self._deps
        previous = await deps.hub.attach(node, link)
        if previous is not None:
            await previous.close()  # A reconnect replaces the old link, which may be half-dead.
        link.start()
        try:
            await deps.hub.announce_online(node, link)
            await _send_terms(link, node, deps.voice)
            await deps.monitor.frame(node, first)
            reason = await link.wait_closed()
            await deps.monitor.link_down(node, link, reason)
            log.info("swarm.link_ended", device_id=node.device_id, reason=reason)
        finally:
            await link.close()

    def _frame_handler(self, node: SwarmNode) -> FrameHandler:
        """Build the callback a link hands the frames nobody was waiting for to."""

        async def handle(envelope: Envelope) -> None:
            # The hub's record is the freshest (a capacity report or a node update replaced it).
            current = self._deps.hub.node(node.device_id) or node
            await self._deps.monitor.frame(current, envelope)

        return handle

    async def _warden(self, connection: Connection, first: Envelope) -> None:
        """Hand a colonized device's Warden connection to the swarm gate, once it proves itself."""
        handler = self._warden_handler
        node = await self._deps.registry.node_by_node_id(first.node_id)
        if handler is None or node is None or not _is_this_warden(node, first):
            return
        kept = pinned(connection, node.node_id)
        await handler.handle(WardenConnection(node, kept.transport, first, kept.frames))

    async def _reply(
        self, connection: Connection, request: Envelope, payload: WaggleMessage
    ) -> bool:
        """Send `payload` back to the sender of `request`, correlated to it; say whether it went."""
        voice = self._deps.voice
        hop = Hop(sender=voice.hive_id, recipient=request.sender, node_id=voice.hive_node_id)
        reply = wrap(payload, hop, clock=self._deps.clock, correlation_id=request.id)
        try:
            # Network write: one small frame on a connection that has just spoken to us.
            async with asyncio.timeout(REPLY_TIMEOUT_S):
                await connection.transport.send(reply)
        except (TimeoutError, TransportClosedError, ConnectionLostError):
            return False
        return True


def _denial(denied: EnrolmentDeniedError) -> ErrorMessage:
    """Build the `control.error` an enrolment denial becomes: ids and a reason, never the token."""
    return ErrorMessage(
        code=denied.code,
        message=str(denied)[:MAX_ERROR_MESSAGE_CHARS],
        failed_kind=ENROL_REQUEST_KIND,
        is_retryable=False,
    )


def _is_this_device(node: SwarmNode, first: Envelope) -> bool:
    """Whether a heartbeat frame is really from the node's device and the node is not revoked."""
    beat = first.payload
    return (
        isinstance(beat, DeviceHeartbeat)
        and node.status is not NodeStatus.REVOKED
        and first.sender == node.device_id
        and beat.device_id == node.device_id
    )


def _is_this_warden(node: SwarmNode, first: Envelope) -> bool:
    """Whether a `cell.ready` frame is from the node's own Warden, naming the node's own Cell."""
    ready = first.payload
    return (
        isinstance(ready, CellReady)
        and node.status is not NodeStatus.REVOKED
        and first.sender == node.warden_id
        and ready.warden_id == node.warden_id
        and ready.cell_id == node.cell_id
    )


async def _send_terms(link: DeviceLink, node: SwarmNode, voice: HiveVoice) -> None:
    """Send a connecting device its current terms, so a change made while it was away arrives."""
    try:
        await link.send(node_update_for(node, ONLINE_TERMS_REASON), sender=voice.hive_id)
    except (GatewayUnavailableError, DeviceTimeoutError):
        return  # The link is already failing; its end is reported by `wait_closed`.
