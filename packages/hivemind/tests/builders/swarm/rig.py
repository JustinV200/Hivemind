"""Wire a Hive-side link to a scripted device in memory: the rig the Swarm tests share.

Testing the Hive's side of a device link needs the whole chain in one place: a signing-free
`MemoryTransport` pair, the Hive end wrapped in a `Connection` and a started `DeviceLink`
attached to a `GatewayHub`, and the device end served by a `ScriptedDevice`. `LinkRig` is that
chain, built by `make_link_rig`, with the clock, the parties, the node, the trail and a log of the
frames nobody awaited (what reaches the link's `on_frame`). `make_source` and `open_rig_session`
build the `SwarmSource` and a `PollenSession` over it. Nothing here opens a socket, so the tests
that use it are fast and deterministic.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the gateway, session,
    source, shield and watch tests, and the SwarmSource entry of the RealCellSource contract.

Key invariants:
    - `close` always ends the link and the device, whatever a test did.
    - The link carries the node's real device id and the Hive's real Queen address, so the
      sender rules the device mimics see the addresses a deployment would.
    - The recorder's identity is the Hive party's, and its trail is in memory.

See Also:
    - builders.swarm.device for the device the rig serves.
    - hivemind.swarm.gateway for the link and the hub.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from builders.cells import make_identity, make_lease_request
from builders.swarm.device import DeviceScript, ScriptedDevice
from builders.swarm.guards import RecordingGuard
from builders.swarm.parties import (
    DeviceParty,
    HiveParty,
    make_device_party,
    make_hive_party,
    make_swarm_node,
)

from hivemind.cell import AccessLevel
from hivemind.cell.lease import RealCellLease
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.gateway import DeviceLink, GatewayHub, HiveVoice, LinkEnds
from hivemind.swarm.gateway.frames import open_connection
from hivemind.swarm.gateway.link import FrameHandler
from hivemind.swarm.models import SwarmNode
from hivemind.swarm.session import PollenSession
from hivemind.swarm.source import SwarmSource
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.transport.memory import MemoryTransport

__all__ = ["LinkRig", "make_link_rig", "make_source", "open_rig_session"]


@dataclass
class LinkRig:
    """A started link to a scripted device, with everything a test inspects."""

    clock: FakeClock
    hive: HiveParty
    party: DeviceParty
    node: SwarmNode
    hub: GatewayHub
    link: DeviceLink
    device: ScriptedDevice
    trail: MemoryPheromoneTrail
    recorder: SwarmRecorder
    unawaited: list[Envelope] = field(default_factory=list)
    heard: asyncio.Event = field(default_factory=asyncio.Event)  # Set after each unawaited frame.

    async def close(self) -> None:
        """End the link and the device; safe to call twice."""
        await self.link.close()
        await self.device.stop()


def _keeper(
    hub: GatewayHub, enrolled: SwarmNode, unawaited: list[Envelope], heard: asyncio.Event
) -> FrameHandler:
    """Build the handler that keeps a frame no exchange was waiting for."""

    async def keep(envelope: Envelope) -> None:
        """Keep the frame, tell the hub's listeners, and signal that one arrived."""
        unawaited.append(envelope)
        await hub.announce_frame(hub.node(enrolled.device_id) or enrolled, envelope)
        heard.set()

    return keep


async def make_link_rig(
    script: DeviceScript | None = None,
    node: SwarmNode | None = None,
    clock: FakeClock | None = None,
    node_overrides: dict[str, object] | None = None,
) -> LinkRig:
    """Build and start a link to a scripted device, attached to a hub.

    Args:
        script: What the device does; the defaults when omitted.
        node: The enrolled node the device is; a SCRATCH Meadow one when omitted.
        clock: The clock everything runs on; a fresh FakeClock when omitted.
        node_overrides: Fields of the generated node to change (its tier, status, access).

    Returns:
        The running rig; the caller awaits `close()` (the `rig` fixture does).
    """
    clock = clock if clock is not None else FakeClock()
    hive, party = make_hive_party(clock), make_device_party(clock)
    enrolled = node if node is not None else make_swarm_node(party, clock, **(node_overrides or {}))
    hive_end, device_end = MemoryTransport.pair(Codec(), Codec())
    unawaited: list[Envelope] = []
    heard, hub = asyncio.Event(), GatewayHub(clock)
    voice = HiveVoice(hive.hive_id, hive.node_id)
    link = DeviceLink(
        open_connection(hive_end, voice, clock),
        LinkEnds(voice, party.device_id),
        clock,
        _keeper(hub, enrolled, unawaited, heard),
    )
    await hub.attach(enrolled, link)
    link.start()
    device = ScriptedDevice(party, hive.hive_id, clock, script)
    device.start(device_end)
    trail = MemoryPheromoneTrail(clock)
    recorder = SwarmRecorder(
        trail, clock, make_identity(clock, hive_id=hive.hive_id, node_id=hive.node_id)
    )
    return LinkRig(
        clock, hive, party, enrolled, hub, link, device, trail, recorder, unawaited, heard
    )


def make_source(
    rig: LinkRig, guard: RecordingGuard | None = None, *, listen: bool = True
) -> SwarmSource:
    """Build the `SwarmSource` for the rig's device, subscribed to its hub unless told not to.

    Args:
        rig: The running rig.
        guard: The guard its sessions use; a fresh allow-all recording guard when omitted.
        listen: Subscribe the source to the hub, so it hears a dead-man release.

    Returns:
        The source.
    """
    source = SwarmSource(
        rig.node, rig.hub, rig.recorder, guard if guard is not None else RecordingGuard()
    )
    if listen:
        rig.hub.subscribe(source)
    return source


async def open_rig_session(
    rig: LinkRig,
    guard: RecordingGuard | None = None,
    *,
    allowed_paths: tuple[Path, ...] = (),
    access: AccessLevel = AccessLevel.SCRATCH,
) -> tuple[PollenSession, RealCellLease, RecordingGuard]:
    """Lease the rig's device and open a `PollenSession` on the lease.

    Args:
        rig: The running rig.
        guard: The guard the session uses; a fresh allow-all recording guard when omitted.
        allowed_paths: Paths outside scratch the lease allows.
        access: The access level the lease asks for.

    Returns:
        The session, its lease and the guard it uses.
    """
    used = guard if guard is not None else RecordingGuard()
    source = make_source(rig, used)
    request = make_lease_request(
        rig.clock,
        cell_id=rig.node.cell_id,
        holder=rig.node.warden_id,
        access_level=access,
        allowed_paths=allowed_paths,
    )
    lease = await source.lease(request)
    session = await source.open_session(lease)
    assert isinstance(session, PollenSession)
    return session, lease, used
