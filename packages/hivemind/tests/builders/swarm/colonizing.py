"""Build the two scripted halves of a colonization: a device's packet, and its own Warden.

Colonizing a device is a conversation between the Queen and two things on the device: its Pollen
Packet, which installs the runtime and launches a process on `swarm.colonize`, and the Warden that
process becomes, which dials the swarm listener's door. Neither needs to be real to test the
Queen's side. `ScriptedPacket` answers each `swarm.colonize` order from a script (the outcome per
step, or silence), records the orders and the `swarm.node_update`s it was sent, and can run a hook
once START is answered, which is where a scenario makes the Warden dial in. `dial_warden` is that
dial: it hands the gate's door a verified connection exactly as the listener's router does, and
returns the Warden's end, a `WardenEnd`, for the test to read the Queen's orders from and answer.
`attach_device_warden` skips the door and attaches a device-style Warden link straight to the
Queen, for a test of the hold that only needs her to hold such a Warden.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    tests/unit/queen/swarm_gate that colonize, withdraw, hold or revoke a device.

Key invariants:
    - A packet answers every order it is scripted to answer, correlated to it, on the device end
      of the very link the gate's hub holds; nothing here opens a socket.
    - A dialled Warden's link proves the device's node, so it is a remote Warden to the Queen (a
      Handoff travels inline to it).
    - `close` ends every task started here, whatever a test did.

See Also:
    - builders.swarm.gate for the gate rig these attach to.
    - hivemind.queen.swarm_gate.attach for the door `dial_warden` dials.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass

from builders.isolation import place_running
from builders.queen import WardenEnd, make_warden_link
from builders.supervision import make_telemetry
from builders.swarm.gate import GateRig, IdleDevice
from builders.swarm.parties import DeviceParty, make_cell_ready

from hivemind.brood_chamber import Task
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate import DeviceWardenDoor
from hivemind.swarm import SwarmNode, WardenConnection, readable_roots
from hivemind.swarm.enrolment import node_update_for
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import HiveId, MessageId, WardenId
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.messages.swarm import (
    Colonize,
    ColonizeAction,
    Colonized,
    ColonizeOutcome,
    NodeUpdate,
    RuntimeLevel,
)
from waggle.signing import Ed25519Signer
from waggle.transport.memory import MemoryTransport

__all__ = [
    "DialledWarden",
    "PlacedDevice",
    "ScriptedPacket",
    "attach_device_warden",
    "dial_warden",
    "place_on_device",
    "report_state",
]

_DEFAULT_OUTCOMES = {
    ColonizeAction.PREPARE: ColonizeOutcome.PREPARED,
    ColonizeAction.START: ColonizeOutcome.STARTED,
    ColonizeAction.WITHDRAW: ColonizeOutcome.WITHDRAWN,
}
_PACKET_RUNTIME = "1.0.0"  # The runtime version a packet reports installed or running.
_WARDEN_PID = 4242  # The process id a packet reports for the Warden it started.
_CLOSE_GRACE_S = 0.2  # Real seconds the door gets to let a closed Warden go before the link drops.


@dataclass
class DialledWarden:
    """A device's own Warden connected to the gate's door: its end, and the door's handler."""

    end: WardenEnd
    handler: asyncio.Task[None]
    transport: MemoryTransport

    async def close(self) -> None:
        """Close the Warden's end of the connection and wait for the door to let it go."""
        await self.transport.close()
        done, _ = await asyncio.wait({self.handler}, timeout=_CLOSE_GRACE_S)
        if not done:
            # The Queen closed her end first and this end consumed that close, so its own close was
            # a no-op and the Queen's reader, already waiting, was never woken: a real socket ends
            # both sides at once. Dropping the link wakes it, as the socket would have.
            self.transport.drop()
        await asyncio.gather(self.handler, return_exceptions=True)


class ScriptedPacket:
    """The packet of a connected device, answering `swarm.colonize` from a script."""

    def __init__(self, device: IdleDevice, clock: FakeClock, hive_id: HiveId) -> None:
        """Start reading the device end of `device`'s link.

        Args:
            device: A connected device from `GateRig.connect`; its far end is read here, so a test
                must not read it too.
            clock: Mints the ids and times of the replies.
            hive_id: The Queen's address, the recipient of every reply.
        """
        node = device.node
        self._device = device
        self._clock = clock
        self._hop = Hop(sender=node.device_id, recipient=hive_id, node_id=node.node_id)
        self.outcomes: dict[ColonizeAction, ColonizeOutcome] = dict(_DEFAULT_OUTCOMES)
        self.silent: set[ColonizeAction] = set()  # Orders it never answers.
        self.refused: set[ColonizeAction] = set()  # Orders it answers with a `control.error`.
        self.wrong: set[ColonizeAction] = set()  # Orders it answers with the wrong message.
        self.orders: list[Colonize] = []
        self.updates: list[NodeUpdate] = []
        self.log: list[str] = []  # What arrived, oldest first: "update" and "colonize:<ACTION>".
        self.on_start: Callable[[Colonize], Awaitable[None]] | None = None  # After its reply.
        self.before: dict[ColonizeAction, Callable[[Colonize], Awaitable[None]]] = {}  # Before it.
        self._task = asyncio.create_task(self._run())

    @property
    def actions(self) -> list[ColonizeAction]:
        """The steps ordered so far, oldest first."""
        return [order.action for order in self.orders]

    async def close(self) -> None:
        """Stop reading the device end."""
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        """Read every frame the Hive sends the device; answer orders, note updates."""
        async for envelope in self._device.device_end.receive():
            payload = envelope.payload
            if isinstance(payload, Colonize):
                self.log.append(f"colonize:{payload.action.value}")
                await self._answer(envelope, payload)
            elif isinstance(payload, NodeUpdate):
                self.log.append("update")
                self.updates.append(payload)

    async def _answer(self, envelope: Envelope, order: Colonize) -> None:
        """Record `order`, reply as scripted, and run the START hook once START was answered."""
        self.orders.append(order)
        before = self.before.get(order.action)
        if before is not None:
            await before(order)
        if order.action in self.silent:
            return
        if order.action in self.refused:
            await self._send(self._error(envelope), envelope.id)
            return
        if order.action in self.wrong:
            await self._send(self._not_colonized(order), envelope.id)
            return
        outcome = self.outcomes[order.action]
        await self._send(self._reply(order, outcome), envelope.id)
        if order.action is ColonizeAction.START and self.on_start is not None:
            await self.on_start(order)

    def _reply(self, order: Colonize, outcome: ColonizeOutcome) -> Colonized:
        """Build the `swarm.colonized` that reports `outcome` for `order`."""
        started = outcome is ColonizeOutcome.STARTED
        installed = outcome in (ColonizeOutcome.PREPARED, ColonizeOutcome.STARTED)
        return Colonized(
            cell_id=order.cell_id,
            device_id=order.device_id,
            action=order.action,
            outcome=outcome,
            level=RuntimeLevel.COLONIZED if started else RuntimeLevel.GATEWAY,
            runtime_version=_PACKET_RUNTIME if installed else None,
            warden_pid=_WARDEN_PID if started else None,
            reason="Scripted.",
        )

    def _error(self, request: Envelope) -> ErrorMessage:
        """Build the `control.error` a packet answers an order it will not carry out with."""
        return ErrorMessage(
            code="hive.swarm.refused",
            message="The scripted packet refuses.",
            failed_kind=request.kind,
            is_retryable=False,
        )

    def _not_colonized(self, order: Colonize) -> NodeUpdate:
        """Build a reply that is not a `swarm.colonized` at all: a node update."""
        return node_update_for(self._device.node, "Scripted.")

    async def _send(self, reply: WaggleMessage, correlation_id: MessageId) -> None:
        """Send `reply` back over the link, correlated to the order it answers."""
        envelope = wrap(reply, self._hop, clock=self._clock, correlation_id=correlation_id)
        await self._device.device_end.send(envelope)


async def dial_warden(
    rig: GateRig,
    node: SwarmNode,
    *,
    door: DeviceWardenDoor | None = None,
    sender: WardenId | None = None,
    **ready: object,
) -> DialledWarden:
    """Connect `node`'s own Warden to the gate's door, as the swarm listener's router would.

    Args:
        rig: The gate rig, whose door (`rig.door`, built by `rig.bind` or `rig.serving`) is dialled.
        node: The device as the registry holds it.
        door: A door to dial instead of the rig's own (one over a Queen whose Guard refuses).
        sender: The address the Warden's first frame is sent from, when it is not the node's own.
        **ready: Fields of the `cell.ready` it sends, replacing the node's own (a wrong Cell, or
            terms other than the node's, as a Warden started before the operator changed them).

    Returns:
        The Warden's end of the connection, and the door's handler task, running.
    """
    active = door if door is not None else (rig.door if rig.door is not None else rig.bind())
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(
        sender=sender or node.warden_id, recipient=rig.hive.voice.hive_id, node_id=node.node_id
    )
    party = DeviceParty(
        device_id=node.device_id,
        node_id=node.node_id,
        cell_id=node.cell_id,
        warden_id=node.warden_id,
        signer=Ed25519Signer.generate(),
    )
    # A Warden is started under the terms its node holds, unless a test says it was started earlier.
    terms: dict[str, object] = {
        "access_level": node.access_level.to_wire(),
        "comb_shield": node.comb_shield.to_wire(),
        # The directories the packet was told, which it hands to the Warden it starts.
        "watch_roots": readable_roots(node),
    }
    first = wrap(make_cell_ready(party, **{**terms, **ready}), hop, clock=rig.clock)
    connection = WardenConnection(node, queen_end, first, _frames(queen_end))
    handler = asyncio.create_task(active.handle(connection))
    return DialledWarden(WardenEnd(warden_end, hop, rig.clock), handler, warden_end)


async def attach_device_warden(rig: GateRig, node: SwarmNode) -> tuple[WardenLink, WardenEnd]:
    """Attach a device-style Warden link to the Queen directly, without the door.

    Args:
        rig: The gate rig, whose Queen the Warden is attached to.
        node: The device whose Warden it is; the link proves the device's node.

    Returns:
        The Queen's link to it, and the Warden's end.
    """
    identity = rig.queen_deps.identity
    link, end = make_warden_link(
        identity.hive_id, node.warden_id, node.node_id, node.as_cell(), rig.clock
    )
    link = dataclasses.replace(link, node_id=node.node_id)
    await rig.queen.attach_warden(link)
    return link, end


@dataclass
class PlacedDevice:
    """A device with its Warden attached to the Queen and one task RUNNING on it."""

    node: SwarmNode
    device: IdleDevice
    link: WardenLink
    task: Task
    end: WardenEnd | None = None


async def place_on_device(
    rig: GateRig, level: RuntimeLevel = RuntimeLevel.GATEWAY, **overrides: object
) -> PlacedDevice:
    """Enrol a device on `level`, give it a Warden, and put a task RUNNING on that Warden.

    A Level 0 device is connected and given a real Warden by the gate's table (`rig.serving()`
    must be open); a higher rung gets a device-style Warden link attached to the Queen directly.

    Args:
        rig: The gate rig.
        level: The rung the device is on.
        **overrides: Fields of the enrolled node to replace.

    Returns:
        The device as enrolled and its connected packet, the Queen's link to its Warden, the
        RUNNING task, and (for a rung above Level 0) the Warden's end of the link.
    """
    node = await rig.enrol(level=level, **overrides)
    device = await rig.connect(node)  # The packet is up at every rung.
    end: WardenEnd | None = None
    if level is RuntimeLevel.GATEWAY:
        assert await rig.wardens.serve(node)
        link = next(link for link in rig.queen.wardens if link.warden_id == node.warden_id)
    else:
        link, end = await attach_device_warden(rig, node)
    task = await place_running(rig.queen_deps, link)
    return PlacedDevice(node, device, link, task, end)


def report_state(rig: GateRig, node: SwarmNode, state: WardenState) -> None:
    """Make the Queen hold that `node`'s Warden last reported `state` in a Heartbeat.

    Args:
        rig: The gate rig, whose Queen is told.
        node: The device whose Warden reported.
        state: What it said it was doing (`WATCH` while it holds no work and observes).
    """
    rig.queen._last_heartbeat[node.warden_id] = Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=state,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


async def _frames(transport: MemoryTransport) -> AsyncGenerator[Envelope, None]:
    """Yield every frame a transport receives, as the router's pinned connection does."""
    async for envelope in transport.receive():
        yield envelope
