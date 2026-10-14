"""Define DeviceWardens: one Level 0 Warden for each Swarm device the gate serves, and its factory.

At Level 0 of the runtime ladder (ADR-0052) a device carries only a Pollen Packet, so its Warden
runs here, on the Hive Stand, in the Queen's own process, and reaches the device through the
`PollenSession` of its `SwarmSource`. This module is where those Wardens live. A
`DeviceWardenFactory`, injected by the composition root so the Queen's layer never learns how a
Warden's collaborators are made, builds the Warden and the Queen's end of its link for one node.
`DeviceWardens.serve` attaches that link to the Queen (her `warden_spawn` point, so a Guard that
refuses it refuses the device), starts the Warden (it leases the device) and runs it as a task of
the gate's `TaskGroup`. `stop` is the reverse, in an order that leaves the device as found: the
Warden stops first, releasing its lease while the packet's link is still up, and only then does the
Queen forget it and its link close.

A node already served is never served twice, so a device that drops and comes back keeps the Warden
it had (its lease is the device's dead-man switch's to settle, or the Warden's to release). A
Warden whose run loop ended on a device error stays in the table until the gate notices
(`ServedDevice.has_ended`) and replaces it; the device failing never ends the Hive.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Driven by the gate's reconcile pass (`hivemind.queen.swarm_gate.reconcile`) and stopped by the
    gate. Calls into `hivemind.cell` (CellError), `hivemind.queen.attach` and `.errors`,
    `hivemind.swarm` (the hub, nodes and errors) and `hivemind.wardens` (Warden) only; the factory
    that builds the Warden lives in `hivemind.cli.compose.swarm`.

Key invariants:
    - A device is in the table from the moment the Queen accepted its Warden's link and the Warden
      started, until `stop` begins: `stop` removes it first, then tears the Warden down, so a
      device being stopped is never served again by a second caller; the Queen forgets the Warden
      only when the teardown is done.
    - A `stop` that is cancelled half-way puts its Warden back in the table, so `stop_all` still
      stops it: a Warden never runs on with no entry anyone can find.
    - `stop` and `stop_all` never leave a task behind: the Warden's run task is awaited, and a
      Warden that cannot release its lease within `STOP_TIMEOUT_S` is abandoned with the lease
      ORPHANED (the device's own dead-man switch settles it), never waited on forever.
    - The hub listeners a built Warden asks for (its `SwarmSource`, which must hear a dead-man
      release) are subscribed before the Warden starts and unsubscribed when it stops.
    - Calls are serialised by the gate (one reconcile pass at a time, under its lock) and by the
      flow that owns a device (the pass leaves such a device alone), so a device is only ever
      touched by one caller at a time; this class holds no lock of its own.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for Level 0 and why the Warden is here.
    - hivemind.cli.compose.swarm.device_warden for the factory the composition root injects.
    - hivemind.queen.attach for attach_warden and detach_warden, the Queen's side of a link.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from hivemind.cell import CellError
from hivemind.common.logging import get_logger
from hivemind.queen.attach import current_link, detach_warden
from hivemind.queen.errors import WardenSpawnRefusedError
from hivemind.swarm import GatewayHub, HubListener, SwarmError, SwarmNode
from hivemind.wardens import Warden
from waggle.ids import DeviceId, WardenId

if TYPE_CHECKING:
    from hivemind.queen.deps import WardenLink
    from hivemind.queen.queen import Queen

# Seconds a Warden gets to stop. Releasing its lease is the slow part: the device has
# `hivemind.swarm.source.RELEASE_REPLY_TIMEOUT_S` (120 s) to confirm it, and this is a margin more,
# so a hung device delays shutdown by that long and not by forever.
STOP_TIMEOUT_S = 150.0

log = get_logger(__name__)

__all__ = ["STOP_TIMEOUT_S", "DeviceWarden", "DeviceWardenFactory", "DeviceWardens", "ServedDevice"]


@dataclass(frozen=True, slots=True)
class DeviceWarden:
    """A Warden built for one device, not yet attached or started.

    Attributes:
        warden: The Level 0 Warden, whose Cell is the device.
        link: The Queen's end of the Warden's link, ready for `Queen.attach_warden`.
        hub_listeners: What must hear the hub while this Warden lives; its `SwarmSource`, which
            releases a lease the device's dead-man switch already released.
    """

    warden: Warden
    link: WardenLink
    hub_listeners: tuple[HubListener, ...] = ()


class DeviceWardenFactory(Protocol):
    """Build the Level 0 Warden of one enrolled device."""

    def build(self, node: SwarmNode) -> DeviceWarden:
        """Build a Warden, and the Queen's end of its link, for `node`.

        Args:
            node: The device as the registry holds it now: its Cell carries these values.

        Returns:
            The Warden, its link and the hub listeners that live and die with it.
        """
        ...


@dataclass(frozen=True, slots=True)
class ServedDevice:
    """One device the gate serves: its Warden, the node it was built from and its run task.

    Attributes:
        built: The Warden and its link.
        node: The node the Warden was built from; compared with the registry's to see whether the
            device's terms changed.
        task: The Warden's run loop, a task of the gate's `TaskGroup`.
    """

    built: DeviceWarden
    node: SwarmNode
    task: asyncio.Task[None]

    @property
    def warden(self) -> Warden:
        """The device's Warden."""
        return self.built.warden

    @property
    def has_ended(self) -> bool:
        """Whether the Warden's run loop is over without anyone having stopped it."""
        return self.task.done()


class DeviceWardens:
    """The table of device Wardens the gate serves, keyed by device."""

    def __init__(self, factory: DeviceWardenFactory, hub: GatewayHub) -> None:
        """Build an empty table.

        Args:
            factory: Builds each device's Warden.
            hub: Where a built Warden's listeners are subscribed.
        """
        self._factory = factory
        self._hub = hub
        self._queen: Queen | None = None
        self._group: asyncio.TaskGroup | None = None
        self._served: dict[DeviceId, ServedDevice] = {}

    def bind(self, queen: Queen) -> None:
        """Attach every Warden this table serves to `queen` from now on."""
        self._queen = queen

    def open(self, group: asyncio.TaskGroup) -> None:
        """Run every Warden served from now on as a task of `group`, the gate's own."""
        self._group = group

    def close(self) -> None:
        """Stop running Wardens in a group that is over; later `serve` calls do nothing."""
        self._group = None

    def get(self, device_id: DeviceId) -> ServedDevice | None:
        """Return the device's served Warden, or None when it has none."""
        return self._served.get(device_id)

    def link_of(self, warden_id: WardenId) -> WardenLink | None:
        """Return the link the Queen holds for `warden_id`, or None when she holds none.

        This is the Queen's own table, not this one's: it finds a Warden that dialled in from its
        device (Level 1 and up) as well as one served here, and a Warden whose link has ended
        but whose entry the Queen keeps for its return.
        """
        queen = self._queen
        return None if queen is None else current_link(queen, warden_id)

    def device_ids(self) -> tuple[DeviceId, ...]:
        """Return every device that has a Warden, in the order they were served."""
        return tuple(self._served)

    async def serve(self, node: SwarmNode) -> bool:
        """Build, attach, start and run `node`'s Warden, unless it already has one.

        Args:
            node: The device as the registry holds it now.

        Returns:
            True when a Warden was started; False when the device already has one, the table is
            not running, or the Queen's Guard refused the Warden (`guard.denied` is on the trail).
        """
        queen, group = self._queen, self._group
        if queen is None or group is None or node.device_id in self._served:
            return False
        built = self._factory.build(node)
        try:
            await queen.attach_warden(built.link)
        except WardenSpawnRefusedError:
            # The Guard's own refusal is already on the trail; the device simply gets no Warden.
            log.error("swarm.warden_refused", device_id=node.device_id)
            await built.link.transport.close()
            return False
        # Subscribed before the Warden leases anything: a dead-man release can arrive at any time.
        for listener in built.hub_listeners:
            self._hub.subscribe(listener)
        await built.warden.start()
        task = group.create_task(self._run(node.device_id, built.warden))
        self._served[node.device_id] = ServedDevice(built, node, task)
        return True

    async def stop(self, device_id: DeviceId) -> bool:
        """Stop the device's Warden, release its lease, detach it and close its link.

        Args:
            device_id: The device.

        Returns:
            True when the device had a Warden; False when it had none.
        """
        served = self._served.pop(device_id, None)
        if served is None:
            return False
        try:
            await self._teardown(served)
        except asyncio.CancelledError:
            # Cancelled half-way (a colonization cut short by the gate stopping): the Warden is
            # not gone, so it goes back in the table for `stop_all` to finish stopping.
            self._served[device_id] = served
            raise
        return True

    async def stop_all(self) -> None:
        """Stop every device's Warden at once, each releasing its lease while its link is up."""
        served = tuple(self._served.values())
        self._served.clear()
        # Concurrently: each stop waits on its own device, and one slow device must not hold the
        # others (or the Hive's shutdown) up for the whole of its release timeout.
        async with asyncio.TaskGroup() as group:
            for entry in served:
                group.create_task(self._teardown(entry))

    async def _run(self, device_id: DeviceId, warden: Warden) -> None:
        """Run `warden` until it is stopped, or until a device error ends it."""
        try:
            await warden.run()
        except (CellError, SwarmError) as error:
            # A device is somebody's machine and goes away mid-command; that ends this Warden's
            # loop, never the Hive's. The gate sees `has_ended` and replaces the Warden.
            log.error("swarm.warden_failed", device_id=device_id, error=type(error).__name__)

    async def _teardown(self, served: ServedDevice) -> None:
        """Stop a Warden, wait for its loop, then let the Queen and the hub forget it."""
        warden = served.warden
        try:
            async with asyncio.timeout(STOP_TIMEOUT_S):
                await warden.stop()
        except (CellError, SwarmError, TimeoutError) as error:
            # The lease could not be confirmed released (the link is down, the device is silent):
            # it stays ORPHANED, and the device's dead-man switch ends it. Say so and carry on.
            log.warning(
                "swarm.warden_stop_failed",
                device_id=served.node.device_id,
                error=type(error).__name__,
            )
        # `stop` set the loop's flag first, so the task ends on its own; a bug in it surfaces here.
        await served.task
        queen = self._queen
        if queen is not None:
            await detach_warden(queen, served.built.link.warden_id)
        for listener in served.built.hub_listeners:
            self._hub.unsubscribe(listener)
        await served.built.link.transport.close()
