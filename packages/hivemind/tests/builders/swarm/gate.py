"""Build the Queen's swarm gate over fakes: a Queen, a registry, a hub, idle links, real Wardens.

The gate's tests need a whole Level 0 world but not a real device: a real `Queen` for Wardens to be
attached to, a Hive Stand's registry and hub (`builders.swarm.hive.HiveRig`) and a listener over
them (a spy, so a test sees what the gate asked it to do), a link to each "device" that is open but
silent (`IdleDevice`; its far end is kept so a test can read what the Hive sent), and a factory
that builds real `Warden`s over a `FakeCellSource` exactly as the composition root builds them
over a `SwarmSource`: the node's own Warden id, `DeviceDeps`, and a link to the Queen. `GateRig`
ties them together and offers the three verbs a scenario needs: `enrol` a node, `connect` it,
`disconnect` it. Nothing here opens a socket or runs a process, so the tests that use it are fast
and deterministic.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    tests/unit/queen/swarm_gate.

Key invariants:
    - Everything runs on one FakeClock: the gate's interval is a call to `clock.advance`, never a
      sleep.
    - A factory-built Warden is the real class over a `FakeCellSource` seeded with the node's own
      Cell, so a lease, a release and a Cell lost are the real Warden's behaviour.
    - `close` always stops the gate, the Wardens and every link, whatever a test did.

See Also:
    - builders.swarm.hive for the Hive Stand's registry, door and hub this builds on.
    - builders.wardens for the Warden deps builder the factory uses.
    - hivemind.queen.swarm_gate for the gate under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace

from builders.queen import make_queen_deps
from builders.swarm.enrolment import enrol_node
from builders.swarm.hive import HiveRig, make_hive_rig
from builders.swarm.parties import make_device_party, make_swarm_node
from builders.wardens import make_warden_deps

from hivemind.cell import AccessLevel, RealCellSource
from hivemind.queen import Queen, QueenDeps, WardenLink
from hivemind.queen.swarm_gate import (
    DeviceReconciler,
    DeviceWarden,
    DeviceWardens,
    ReconcileDeps,
    SwarmGate,
    UnreachableDevices,
)
from hivemind.swarm import DeviceLink, HubListener, SwarmListener, SwarmNode
from hivemind.swarm.gateway import LinkEnds, ListenerConfig, ListenerDeps, LivenessRules
from hivemind.swarm.gateway.frames import open_connection
from hivemind.wardens import DeviceDeps, Warden
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop
from waggle.ids import DeviceId
from waggle.transport.memory import MemoryTransport

__all__ = [
    "INTERVAL_S",
    "GateRig",
    "IdleDevice",
    "RecordingFactory",
    "SpyListener",
    "make_gate_rig",
]

INTERVAL_S = 15.0  # The gate's reconcile interval in these tests: `[swarm] heartbeat_interval_s`.


class SpyListener(SwarmListener):
    """A swarm listener that counts what the gate asks of it, and can note when it is stopped."""

    def __init__(self, config: ListenerConfig, deps: ListenerDeps) -> None:
        """Build the listener; nothing is bound until `start`."""
        super().__init__(config, deps)
        self.disconnected: list[DeviceId] = []
        self.key_syncs = 0
        self.on_stop: Callable[[], None] = lambda: None

    async def disconnect(self, device_id: DeviceId) -> bool:
        """Note the device, then close its link as the real listener does."""
        self.disconnected.append(device_id)
        return await super().disconnect(device_id)

    async def sync_keys(self) -> None:
        """Count the call, then re-read the keys as the real listener does."""
        self.key_syncs += 1
        await super().sync_keys()

    async def stop(self) -> None:
        """Run the test's hook first (it sees the world as the listener is about to close)."""
        self.on_stop()
        await super().stop()


@dataclass
class IdleDevice:
    """An open link to a device that says nothing: what the Hive sent is read at `device_end`."""

    node: SwarmNode
    link: DeviceLink
    device_end: MemoryTransport

    async def sent(self) -> Envelope:
        """Return the next frame the Hive sent this device."""
        return await anext(self.device_end.receive())


class RecordingFactory:
    """Build real Level 0 Wardens over a `FakeCellSource`, and remember each one it built."""

    def __init__(self, queen_deps: QueenDeps, clock: FakeClock) -> None:
        """Build the factory for a Queen's identity.

        Args:
            queen_deps: The Queen the Wardens will be attached to; her ids address the links.
            clock: The clock every Warden runs on.
        """
        self._queen_deps = queen_deps
        self._clock = clock
        self.built: list[DeviceWarden] = []
        self.nodes: list[SwarmNode] = []
        self.listeners: tuple[HubListener, ...] = ()
        self.source_for: Callable[[RealCellSource], RealCellSource] = lambda source: source
        self.warden_class: type[Warden] = Warden

    def build(self, node: SwarmNode) -> DeviceWarden:
        """Build `node`'s Warden over the node's own Cell, and the Queen's end of its link."""
        identity = self._queen_deps.identity
        queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
        cell = node.as_cell()
        hop = Hop(sender=node.warden_id, recipient=identity.hive_id, node_id=identity.node_id)
        deps, _unused, _id = make_warden_deps(
            self._clock,
            cells=(cell,),
            warden_id=node.warden_id,
            queen_link=warden_end,
            hop=hop,
            device=DeviceDeps(),
        )
        deps = replace(deps, source=self.source_for(deps.source))
        queen_hop = Hop(sender=identity.hive_id, recipient=node.warden_id, node_id=identity.node_id)
        link = WardenLink(warden_id=node.warden_id, cell=cell, transport=queen_end, hop=queen_hop)
        built = DeviceWarden(self.warden_class(node.warden_id, deps), link, self.listeners)
        self.built.append(built)
        self.nodes.append(node)
        return built


@dataclass
class GateRig:
    """A gate and everything it works with, over fakes."""

    clock: FakeClock
    hive: HiveRig
    queen: Queen
    queen_deps: QueenDeps
    factory: RecordingFactory
    listener: SpyListener
    wardens: DeviceWardens
    reconciler: DeviceReconciler
    unreachable: UnreachableDevices
    gate: SwarmGate
    devices: dict[DeviceId, IdleDevice] = field(default_factory=dict)

    async def enrol(self, **overrides: object) -> SwarmNode:
        """Add a node (ONLINE, FULL access by default) to the registry; it is not connected yet."""
        party = make_device_party(self.clock)
        node = make_swarm_node(party, self.clock, **{"access_level": AccessLevel.FULL, **overrides})
        return await enrol_node(self.hive.registry, node, self.clock)

    async def connect(self, node: SwarmNode) -> IdleDevice:
        """Attach an open, silent link to `node` on the hub, as the listener does when it dials."""
        hive_end, device_end = MemoryTransport.pair(Codec(), Codec())
        voice = self.hive.voice
        link = DeviceLink(
            open_connection(hive_end, voice, self.clock),
            LinkEnds(voice, node.device_id),
            self.clock,
            _ignore,
        )
        await self.hive.hub.attach(node, link)
        link.start()
        device = IdleDevice(node, link, device_end)
        self.devices[node.device_id] = device
        return device

    async def disconnect(self, node: SwarmNode) -> None:
        """Close `node`'s link and detach it, as the listener does when it ends."""
        device = self.devices.pop(node.device_id)
        await device.link.close()
        self.hive.hub.detach(node.device_id, device.link)
        await device.device_end.close()

    @asynccontextmanager
    async def serving(self, queen: Queen | None = None) -> AsyncIterator[None]:
        """Run the table of device Wardens, bound to `queen`, for the length of the block.

        Args:
            queen: The Queen the Wardens attach to; the rig's own when omitted.

        Yields:
            Control, with the table ready to `serve` nodes; every Warden is stopped on the way out.
        """
        self.wardens.bind(queen if queen is not None else self.queen)
        async with asyncio.TaskGroup() as group:
            self.wardens.open(group)
            try:
                yield
            finally:
                await self.wardens.stop_all()
                self.wardens.close()

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        """Start the gate against the rig's Queen and run it, stopping it on the way out.

        Yields:
            Control, with the gate's listener open and its loop (and so every Warden) running.
        """
        await self.gate.start(self.queen)
        task = asyncio.create_task(self.gate.run())
        try:
            yield
        finally:
            await self.gate.stop()
            await task

    async def close(self) -> None:
        """Stop the gate and every Warden, close every link, and stop the Queen's readers."""
        await self.gate.stop()
        for device in tuple(self.devices.values()):
            await device.link.close()
            await device.device_end.close()
        await self.queen.stop()


async def _ignore(envelope: Envelope) -> None:
    """Take a frame nobody was waiting for and do nothing with it."""


def make_gate_rig() -> GateRig:
    """Build a gate over a fresh Queen, registry, hub and factory; nothing is started.

    Returns:
        The rig. A test that runs the gate awaits `gate.start(rig.queen)` and runs `gate.run()`;
        every test awaits `close()` (a fixture does).
    """
    clock = FakeClock()
    hive = make_hive_rig(clock)
    queen_deps, _link, _end = make_queen_deps(clock=clock)
    queen = Queen(queen_deps)
    factory = RecordingFactory(queen_deps, clock)
    config = ListenerConfig(
        host="127.0.0.1", port=0, tls_cert=None, tls_key=None, liveness=LivenessRules(4)
    )
    listener = SpyListener(
        config,
        ListenerDeps(
            hive.registry, hive.door, hive.hub, hive.recorder, hive.voice, hive.hive.signer
        ),
    )
    wardens = DeviceWardens(factory, hive.hub)
    unreachable = UnreachableDevices(wardens)
    reconciler = DeviceReconciler(ReconcileDeps(hive.registry, listener, wardens, unreachable))
    gate = SwarmGate(listener, hive.registry, wardens, INTERVAL_S, clock)
    return GateRig(
        clock, hive, queen, queen_deps, factory, listener, wardens, reconciler, unreachable, gate
    )
