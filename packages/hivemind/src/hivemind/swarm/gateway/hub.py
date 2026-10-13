"""Define GatewayHub and HubListener: which devices are connected, and who hears about them.

The hub is the Hive's directory of live device links. The swarm listener attaches a link to it
when a device's first frame verifies and detaches it when the link ends; everything else that
needs a device asks the hub: `link(device_id)` for the connection to a device, `link_for_cell`
for the device behind a Real Cell (a `SwarmSource` knows its Cell, not its link), `wait_for` to
wait for a device that is about to connect. A device that reconnects replaces its previous link,
and the hub hands the old one back so the caller can close it. Anything that wants to react to a
device coming or going, or to a frame nobody was waiting for (a capacity report, a dead-man
switch's `cell.lease_released`), subscribes a `HubListener`; the Queen's swarm gate is the main
one, starting a device's Warden on `on_online` and stopping it on `on_offline`. The hub itself
only remembers and announces; the liveness rules live in `hivemind.swarm.gateway.heartbeat`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.gateway`.
    Written by the listener and the heartbeat monitor; read by `SwarmSource`, the watch probe and
    the Queen's swarm gate (Layer 6), which subscribes. Calls into hivemind.swarm.models,
    .errors and waggle only.

Key invariants:
    - At most one link per device is attached; attaching a second returns the first, and
      detaching a link that is no longer the current one does nothing, so a stale link ending
      after its replacement connected can never take the device offline.
    - Listeners are called in subscription order, one at a time, and a listener's exception
      propagates to the caller: a bug in a listener is loud, not swallowed.
    - `link` and `link_for_cell` return only links that are still open.

See Also:
    - hivemind.swarm.gateway.link for the connection a hub entry holds.
    - hivemind.swarm.gateway.heartbeat for the monitor that drives the announcements.
    - hivemind.queen.cell_gate.provider for the same wait-for-readiness shape on Virtual Cells.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hivemind.swarm.errors import GatewayUnavailableError
from hivemind.swarm.gateway.link import DeviceLink
from hivemind.swarm.models import SwarmNode
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.ids import CellId, DeviceId

__all__ = ["GatewayHub", "HubListener"]


class HubListener(Protocol):
    """Hear about devices coming and going and about frames nobody was waiting for."""

    async def on_online(self, node: SwarmNode, link: DeviceLink) -> None:
        """A device is connected and reachable (it connected, or its heartbeats resumed).

        Args:
            node: The node as the registry holds it now.
            link: Its live link.
        """
        ...

    async def on_offline(self, node: SwarmNode, reason: str) -> None:
        """A device is no longer reachable (its link ended, or heartbeats stopped).

        Args:
            node: The node as the registry held it.
            reason: Why, as a short stable word or error code.
        """
        ...

    async def on_frame(self, node: SwarmNode, envelope: Envelope) -> None:
        """A frame arrived that no request was waiting for.

        Args:
            node: The device it came from.
            envelope: A verified frame: a capacity report, a heartbeat, an unprompted lease
                release, or any event the device chose to send.
        """
        ...


@dataclass(slots=True)
class _Entry:
    """One attached device: its latest node record, its link and when it attached."""

    node: SwarmNode
    link: DeviceLink
    attached_at: datetime


class GatewayHub:
    """The Hive's directory of live device links, announced to whoever subscribes.

    Concurrency model: the directory is touched only from tasks on one event loop and never
    across an await, except through `wait_for`, which waits on a condition the attach wakes.
    """

    def __init__(self, clock: Clock) -> None:
        """Build an empty hub.

        Args:
            clock: Stamps when each link attached, which the listener's replay grace reads.
        """
        self._clock = clock
        self._entries: dict[DeviceId, _Entry] = {}
        self._listeners: list[HubListener] = []
        self._changed = asyncio.Condition()  # Woken by every attach, for `wait_for`.

    def subscribe(self, listener: HubListener) -> None:
        """Announce devices coming, going and speaking to `listener` from now on.

        Args:
            listener: Called in subscription order after those already subscribed.
        """
        self._listeners.append(listener)

    def unsubscribe(self, listener: HubListener) -> None:
        """Stop announcing to `listener`; one that was never subscribed is ignored."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def link(self, device_id: DeviceId) -> DeviceLink | None:
        """Return the open link to a device, or None when it is not connected."""
        entry = self._entries.get(device_id)
        return entry.link if entry is not None and entry.link.is_open else None

    def link_for_cell(self, cell_id: CellId) -> DeviceLink | None:
        """Return the open link to the device that is this Real Cell, or None."""
        for entry in self._entries.values():
            if entry.node.cell_id == cell_id and entry.link.is_open:
                return entry.link
        return None

    def node(self, device_id: DeviceId) -> SwarmNode | None:
        """Return the hub's latest record of a connected device, or None."""
        entry = self._entries.get(device_id)
        return entry.node if entry is not None else None

    def attached_at(self, device_id: DeviceId) -> datetime | None:
        """Return when a device's current link attached, or None when it has none."""
        entry = self._entries.get(device_id)
        return entry.attached_at if entry is not None else None

    def attached_at_for_node(self, node_id: str) -> datetime | None:
        """Return when the current link of the device with this node id attached, or None."""
        for entry in self._entries.values():
            if entry.node.node_id == node_id:
                return entry.attached_at
        return None

    def entries(self) -> tuple[tuple[SwarmNode, DeviceLink], ...]:
        """Return every attached device with its link, in no particular order."""
        return tuple((entry.node, entry.link) for entry in self._entries.values())

    async def wait_for(self, device_id: DeviceId, timeout_s: float) -> DeviceLink:
        """Wait until a device has an open link, and return it.

        Args:
            device_id: The device.
            timeout_s: Real seconds to wait.

        Returns:
            The device's open link.

        Raises:
            GatewayUnavailableError: No link opened within `timeout_s`.
        """
        try:
            # In-process wait for the listener to attach a link; the bound is how long the
            # caller is willing to wait for a device to dial in.
            async with asyncio.timeout(timeout_s):
                async with self._changed:
                    await self._changed.wait_for(lambda: self.link(device_id) is not None)
        except TimeoutError as exc:
            raise GatewayUnavailableError(
                f"Device {device_id} did not connect within {timeout_s} seconds."
            ) from exc
        link = self.link(device_id)
        if link is None:  # It ended again between the wake and here.
            raise GatewayUnavailableError(f"Device {device_id} disconnected as it connected.")
        return link

    async def attach(self, node: SwarmNode, link: DeviceLink) -> DeviceLink | None:
        """Record `link` as the device's current link, replacing any earlier one.

        Args:
            node: The device's node record.
            link: Its newly verified link.

        Returns:
            The link it replaced, which the caller closes, or None when there was none.
        """
        previous = self._entries.get(node.device_id)
        self._entries[node.device_id] = _Entry(node, link, self._clock.now())
        async with self._changed:
            self._changed.notify_all()
        return previous.link if previous is not None and previous.link is not link else None

    def detach(self, device_id: DeviceId, link: DeviceLink) -> bool:
        """Forget `link` if it is still the device's current link.

        Args:
            device_id: The device.
            link: The link that ended.

        Returns:
            True when it was current and is now forgotten; False when the device had already
            reconnected on another link, which stays attached.
        """
        entry = self._entries.get(device_id)
        if entry is None or entry.link is not link:
            return False
        del self._entries[device_id]
        return True

    def update_node(self, node: SwarmNode) -> None:
        """Refresh the record held for a connected device; a device not attached is ignored."""
        entry = self._entries.get(node.device_id)
        if entry is not None:
            entry.node = node

    async def announce_online(self, node: SwarmNode, link: DeviceLink) -> None:
        """Tell every listener that `node` is reachable on `link`."""
        for listener in tuple(self._listeners):
            await listener.on_online(node, link)

    async def announce_offline(self, node: SwarmNode, reason: str) -> None:
        """Tell every listener that `node` is no longer reachable, and why."""
        for listener in tuple(self._listeners):
            await listener.on_offline(node, reason)

    async def announce_frame(self, node: SwarmNode, envelope: Envelope) -> None:
        """Tell every listener that `node` sent a frame nobody was waiting for."""
        for listener in tuple(self._listeners):
            await listener.on_frame(node, envelope)
