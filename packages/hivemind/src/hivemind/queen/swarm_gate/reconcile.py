"""Define DeviceReconciler: bring the Wardens the gate serves in line with the registry and the hub.

The registry says who is enrolled, at what level and tier, and whether the operator has revoked
them; the hub says who is connected. The gate's job is to make the Wardens it serves match both,
and this is that rule, applied one node at a time and level-triggered: each pass reads the present
state of a node and does whatever is missing, so a notice that was missed, repeated or reordered
costs nothing. For one node:

- A revoked node is cut off. Its link is closed, its Warden stopped, and the listener told to stop
  trusting its key (`swarm.revoked` was recorded by whoever revoked it, so nothing is recorded
  here).
- A node that is online with a live link gets a Warden if it has none (or has lost the one it
  had, because its run loop ended), and keeps the one it has. If the operator changed its access
  level or tier, the device is sent `swarm.node_update` on its live link and the Warden is rebuilt,
  so the Cell it supervises carries the new values.
- A node that is not reachable (its link is gone, or its heartbeats stopped) is reported lost to
  its Warden, which alarms once.

Only nodes on the GATEWAY rung are served here: a device whose Warden has moved onto it connects as
a Warden of its own (a later step), and is no business of Level 0.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Run by the gate's loop, once at every tick and whenever the hub reports a device coming or
    going. Calls into `hivemind.queen.swarm_gate.wardens` and `.unreachable`, and into
    `hivemind.swarm` (the registry, the listener and the node update) only.

Key invariants:
    - One node's failure never stops the pass: it is logged by type and the rest are reconciled.
    - A Warden is rebuilt only after the device has been sent its new terms on the link that is
      up; a send that fails leaves everything as it was for the next pass.
    - The hub's record of a node is refreshed with the registry's before its Warden is rebuilt,
      since `SwarmSource` reads the hub's record first and would otherwise lease on the old terms.
    - The listener is told to re-read the keys once per revocation it sees, not once per pass.

See Also:
    - hivemind.swarm.gateway.heartbeat for who flips a node between ONLINE and UNREACHABLE.
    - hivemind.queen.swarm_gate.unreachable for what a lost device's Warden is told.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import CellError
from hivemind.common.logging import get_logger
from hivemind.queen.swarm_gate.unreachable import UnreachableDevices
from hivemind.queen.swarm_gate.wardens import DeviceWardens, ServedDevice
from hivemind.swarm import (
    DeviceTimeoutError,
    GatewayUnavailableError,
    NodeStatus,
    RuntimeLevel,
    SwarmError,
    SwarmListener,
    SwarmNode,
    SwarmRegistry,
)
from hivemind.swarm.enrolment import node_update_for
from waggle.ids import DeviceId

# What the device is told when the operator changed its terms while it was connected.
TERMS_CHANGED_REASON = "The operator changed this device's access level or Comb Shield tier."

log = get_logger(__name__)

__all__ = ["TERMS_CHANGED_REASON", "DeviceReconciler", "ReconcileDeps"]


@dataclass(frozen=True, slots=True)
class ReconcileDeps:
    """What the reconciler works with (codingrules 5.1).

    Attributes:
        registry: Who is enrolled, at what level and tier, and who is revoked.
        listener: The swarm listener: its hub says who is connected, and it cuts a device off.
        wardens: The table of device Wardens being kept in line.
        unreachable: Told when a device's link is lost and when it is back.
    """

    registry: SwarmRegistry
    listener: SwarmListener
    wardens: DeviceWardens
    unreachable: UnreachableDevices


class DeviceReconciler:
    """Make the device Wardens match the registry and the hub, one node at a time."""

    def __init__(self, deps: ReconcileDeps) -> None:
        """Build the reconciler.

        Args:
            deps: The registry, listener, Wardens and unreachable handler it works with.
        """
        self._deps = deps
        self._revoked: set[DeviceId] = set()

    async def sweep(self) -> None:
        """Reconcile every enrolled node; one that fails is logged and the rest go on."""
        for node in await self._deps.registry.nodes():
            try:
                await self.sync(node)
            except (SwarmError, CellError) as error:
                # The type only: a repr of a device error can carry what the device wrote.
                log.error("swarm.sync_failed", device_id=node.device_id, error=type(error).__name__)

    async def sync(self, node: SwarmNode) -> None:
        """Do whatever `node`'s present state asks of its Warden.

        Args:
            node: The node as the registry holds it now.
        """
        if node.status is NodeStatus.REVOKED:
            await self._revoke(node)
        elif self._is_reachable(node):
            await self._keep(node)
        else:
            await self._deps.unreachable.lost(node)

    def _is_reachable(self, node: SwarmNode) -> bool:
        """Whether the registry calls the device online and the hub holds an open link to it."""
        # Both: a link that is open but silent is UNREACHABLE in the registry (the monitor marks
        # it), and a node the registry still calls ONLINE after a restart has no link yet.
        link = self._deps.listener.hub.link(node.device_id)
        return node.status is NodeStatus.ONLINE and link is not None

    async def _revoke(self, node: SwarmNode) -> None:
        """Cut a revoked device off: close its link, stop its Warden, distrust its key."""
        deps = self._deps
        await deps.listener.disconnect(node.device_id)
        await deps.wardens.stop(node.device_id)
        if node.device_id not in self._revoked:
            self._revoked.add(node.device_id)
            await deps.listener.sync_keys()

    async def _keep(self, node: SwarmNode) -> None:
        """Give a reachable device a Warden, keep it, or rebuild it for changed terms."""
        if node.level is not RuntimeLevel.GATEWAY:
            return  # Its Warden is on the device now (a later step); nothing here serves it.
        wardens = self._deps.wardens
        served = wardens.get(node.device_id)
        if served is not None and served.has_ended:
            await wardens.stop(node.device_id)  # Its loop died of a device error: start afresh.
            served = None
        if served is None:
            await wardens.serve(node)
        elif _terms_changed(served, node):
            await self._update(node)
        else:
            self._deps.unreachable.found(node)

    async def _update(self, node: SwarmNode) -> None:
        """Send the device its new terms, then rebuild its Warden so its Cell carries them."""
        deps = self._deps
        link = deps.listener.hub.link(node.device_id)
        if link is None:
            return  # It went away since the check; the next pass sees that.
        try:
            await link.send(node_update_for(node, TERMS_CHANGED_REASON), sender=link.hive_id)
        except (GatewayUnavailableError, DeviceTimeoutError):
            return  # The link is failing; nothing changed, so the next pass tries again.
        deps.listener.hub.update_node(node)
        await deps.wardens.stop(node.device_id)
        await deps.wardens.serve(node)


def _terms_changed(served: ServedDevice, node: SwarmNode) -> bool:
    """Whether the operator changed the access level or tier the Warden was built with."""
    built = served.node
    return built.access_level is not node.access_level or built.comb_shield is not node.comb_shield
