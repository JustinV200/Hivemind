"""Define DeviceWardenDoor: admit a colonized device's Warden to the Queen through the shared path.

Once a device is colonized (ADR-0052) its Warden runs on the device and dials the swarm listener
itself. The listener (Layer 3) verifies the connection's first frame, a `cell.ready`, and hands it
here as a `WardenConnection`. The door is the gate's policy for that connection, and it decides
three things before the shared attach path (`hivemind.queen.attach.serve_warden`) takes over:

- the connection is this node's own Warden: the sender is the node's `warden_id`, the frame names
  the node's Cell, and the node is not revoked (the listener already checked it; the door does not
  lean on that, since it is the door that lets a Warden into the Queen's tree);
- a Warden is wanted at all: a device on Level 0 has its Warden on the Hive Stand, so one dialling
  in is refused unless the Queen herself just started it (a colonization is waiting for it);
- the Cell it supervises is the device the registry holds, built from the node: REAL, source
  `swarm`, the node's access level, tier and capacity, with that capacity read live from the hub.

What the door tells the gate when a Warden attaches or its connection ends is `DoorEvents`: the
gate wakes its loop, tells a colonization its Warden arrived, and notes why a lost Warden went.
The door never decides what a lost Warden means; that is the hold (`unreachable`), and the Warden's
entry stays with the Queen for it to come back to. Every frame of the connection passes the gate
once before it reaches the Queen (`DoorEvents.heard`): the gate notes the model servers a capacity
report declares, and keeps for itself the one kind that answers its own order (a Nuc promotion's
`swarm.nuc_promoted`), which the Queen's inbox has no rule for. Everything after the handshake
(the fan-out, `warden.spawned` or `warden.reconnected`, the snapshot relay, the signed trail
segment, the refusals) is the shared path's, the same code a Virtual Cell's Warden goes through.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Bound to the swarm listener by `SwarmGate.start` (`listener.bind_warden_handler`), which also
    supplies the events. Calls into `hivemind.pheromone`, `hivemind.queen.attach`,
    `hivemind.queen.deps`, `hivemind.queen.errors`, `hivemind.queen.swarm_gate` (its own modules),
    `hivemind.queen.trail`, `hivemind.swarm` and waggle only.

Key invariants:
    - A connection is attached only if it is the node's own Warden and the gate wants one; any
      other is returned unserved, and the listener closes it.
    - A Warden the Queen's set refuses to spawn is never attached (`guard.denied` is already on
      the trail) and its connection is returned unserved.
    - A segment from a device's Warden must be signed with the node's registered key.
    - A frame the gate consumed (``DoorEvents.heard`` returned True) never reaches the Queen; every
      other frame does, in order, whether or not the gate looked at it.
    - The door holds no state of its own: every decision is read from the connection, the registry
      and the gate's events.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for a device Warden's connection.
    - hivemind.queen.attach.serve for the shared path, and hivemind.queen.cell_gate.listener for
      the Virtual Cell door that uses it too.
    - hivemind.swarm.gateway.routing for the listener side that hands connections here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Protocol

from hivemind.forage import ForageCapacity
from hivemind.pheromone import TrailRecorder, segments_of
from hivemind.queen.attach import LinkHooks, LinkServices, ProvedLink, serve_warden
from hivemind.queen.deps import LiveCapacity, WardenLink
from hivemind.queen.errors import WardenSpawnRefusedError
from hivemind.queen.swarm_gate.colonize import Colonizer
from hivemind.queen.swarm_gate.keys import RegistrySegmentKeys
from hivemind.queen.swarm_gate.terms import GateContext
from hivemind.queen.swarm_gate.unreachable import WARDEN_LINK_REASON, UnreachableDevices
from hivemind.queen.trail import TrailSegmentReceiver
from hivemind.swarm import (
    GatewayHub,
    NodeStatus,
    RuntimeLevel,
    SwarmListener,
    SwarmNode,
    SwarmRegistry,
    WardenConnection,
)
from waggle.envelope import Envelope, Hop
from waggle.messages.cell.status import CellReady

__all__ = ["DeviceWardenDoor", "DoorEvents", "GateDoorEvents", "link_services", "through_the_gate"]


class DoorEvents(Protocol):
    """What the gate decides and wants to hear about a device Warden's connection."""

    def admits(self, node: SwarmNode) -> bool:
        """Whether a Warden dialling in for `node` is wanted right now."""
        ...

    async def attached(self, node: SwarmNode, link: WardenLink, reconnected: bool) -> None:
        """The Queen attached `node`'s Warden under `link`; `reconnected` if she knew it."""
        ...

    async def ended(self, node: SwarmNode, link: WardenLink) -> None:
        """`node`'s Warden's connection ended while `link` was still its current link."""
        ...

    def heard(self, node: SwarmNode, envelope: Envelope) -> bool:
        """Take in one frame of `node`'s Warden; True when it was the gate's and is consumed."""
        ...


class GateDoorEvents:
    """The gate's answers to its door: who is wanted, and what an attach or a loss sets going."""

    def __init__(
        self,
        colonizer: Colonizer,
        unreachable: UnreachableDevices,
        hub: GatewayHub,
        wake: Callable[[], None],
    ) -> None:
        """Build the events over the colonizer, the hold, the hub and the gate's wake-up.

        Args:
            colonizer: Says whether a Level 0 device's Warden is expected, and hears it arrive.
            unreachable: Told, by reason, why a Warden went; its hold begins in the gate's loop.
            hub: Where a reconnecting Warden's outbox replay is noted as expected.
            wake: Wakes the gate's loop so it looks at the device now.
        """
        self._colonizer = colonizer
        self._unreachable = unreachable
        self._hub = hub
        self._wake = wake

    def admits(self, node: SwarmNode) -> bool:
        """Admit a Warden for a colonized device, or a Level 0 one the Queen just started."""
        return node.level is not RuntimeLevel.GATEWAY or self._colonizer.expects(node.device_id)

    async def attached(self, node: SwarmNode, link: WardenLink, reconnected: bool) -> None:
        """Note a returning Warden's replay, tell a waiting colonization, and wake the loop."""
        if reconnected:
            self._hub.note_attach(node.node_id)  # Its outbox replay's duplicates are expected.
        self._colonizer.arrived(node.device_id, link)
        self._wake()  # The loop ends the device's hold, if it had one, on its next pass.

    async def ended(self, node: SwarmNode, link: WardenLink) -> None:
        """Note why the Warden went, stop any wait on it, and wake the loop to hold its tasks."""
        # Only noted here: the hold begins in the loop, from the registry and the Queen's table as
        # they are then, so a Warden that is already back costs nothing.
        self._unreachable.note(node.device_id, WARDEN_LINK_REASON)
        self._colonizer.lost(node.device_id)  # An order sent to it will never be answered.
        self._wake()

    def heard(self, node: SwarmNode, envelope: Envelope) -> bool:
        """Hand the Warden's frame to the colonizer, which says whether the gate consumed it."""
        return self._colonizer.heard(node, envelope)


class DeviceWardenDoor:
    """Serve each verified device Warden connection the listener hands over (the handler)."""

    def __init__(
        self,
        listener: SwarmListener,
        registry: SwarmRegistry,
        context: GateContext,
        events: DoorEvents,
    ) -> None:
        """Build the door; bind it with `listener.bind_warden_handler`.

        Args:
            listener: The swarm listener, whose voice addresses the Queen's frames to the Warden
                and whose hub reads the device's capacity as it stands.
            registry: Holds each node's pinned key, which a Warden's trail segment is checked by.
            context: The Queen the Warden is attached to, and her collaborators.
            events: The gate's answers: who is wanted, and what an attach or a loss sets going.
        """
        self._listener = listener
        self._context = context
        self._events = events
        self._services = link_services(context, registry)

    async def handle(self, connection: WardenConnection) -> None:
        """Attach the connection's Warden and serve its link; return when the connection ends.

        Args:
            connection: The verified connection, its first frame and the one iterator of frames.
        """
        node = connection.node
        if not _is_this_warden(node, connection.first) or not self._events.admits(node):
            return  # The listener closes a connection returned unserved.
        events = self._events

        async def attached(link: WardenLink, reconnected: bool) -> None:
            await events.attached(node, link, reconnected)

        async def ended(link: WardenLink) -> None:
            await events.ended(node, link)

        try:
            await serve_warden(
                self._context.queen,
                self._proved(connection),
                self._services,
                LinkHooks(on_attached=attached, on_ended=ended),
            )
        except WardenSpawnRefusedError:
            return  # The Queen's set refused it: `guard.denied` is on the trail; nothing attached.

    def _proved(self, connection: WardenConnection) -> ProvedLink:
        """Build what the handshake proved: the device's Cell, its node and the Queen's address."""
        node, voice = connection.node, self._listener.voice
        return ProvedLink(
            cell=node.as_cell(),
            node_id=node.node_id,  # The node whose key signed the frames: it speaks for the Cell.
            first=connection.first,
            transport=connection.transport,
            frames=through_the_gate(connection.frames, node, self._events),
            hop=Hop(sender=voice.hive_id, recipient=node.warden_id, node_id=voice.hive_node_id),
            live_capacity=self._live_capacity(node),
        )

    def _live_capacity(self, node: SwarmNode) -> LiveCapacity:
        """Return the reader of the device's capacity as its latest report left it on the hub."""
        hub = self._listener.hub

        async def read() -> ForageCapacity:
            """Read the hub's freshest record of the device, else the one it dialled with."""
            # No I/O: the packet's capacity reports update the hub's record.
            latest = hub.node(node.device_id)
            return (latest or node).capacity

        return read


async def through_the_gate(
    frames: AsyncIterator[Envelope], node: SwarmNode, events: DoorEvents
) -> AsyncIterator[Envelope]:
    """Yield the connection's frames, less those the gate consumed itself.

    The gate looks at every frame once, in order, before the Queen's reader does; what it keeps is
    what answers its own orders, which the Queen has no rule for. Whatever the connection raises
    (a lost link, a forged frame) passes through to the Queen's reader unchanged.
    """
    async for envelope in frames:
        if not events.heard(node, envelope):
            yield envelope


def link_services(context: GateContext, registry: SwarmRegistry) -> LinkServices:
    """Build what the Queen answers and merges on a device Warden's link.

    Args:
        context: The Queen, whose trail a segment merges into and whose clock stamps replies.
        registry: Holds each node's pinned key, which a segment must be signed with.

    Returns:
        The services: a segment receiver that requires a signature, and the recorder for refusals.
        No snapshot handler: a device is borrowed, never snapshotted.
    """
    deps = context.deps
    recorder = TrailRecorder(deps.trail, deps.clock, deps.identity.hive_id, deps.identity.node_id)
    keys = RegistrySegmentKeys(registry)
    receiver = TrailSegmentReceiver(deps.trail, segments_of(deps.trail), keys)
    return LinkServices(clock=deps.clock, recorder=recorder, trail=receiver)


def _is_this_warden(node: SwarmNode, first: Envelope) -> bool:
    """Whether `first` is a `cell.ready` from the node's own Warden, naming the node's own Cell."""
    ready = first.payload
    return (
        isinstance(ready, CellReady)
        and node.status is not NodeStatus.REVOKED
        and first.sender == node.warden_id
        and ready.warden_id == node.warden_id
        and ready.cell_id == node.cell_id
    )
