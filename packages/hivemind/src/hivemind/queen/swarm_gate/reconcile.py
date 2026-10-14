"""Define DeviceReconciler: bring the Wardens the gate serves in line with the registry and the hub.

The registry says who is enrolled, at what rung and tier, whether the operator has revoked them
and what rung they asked for; the hub says who is connected. The gate's job is to make the Wardens
it serves match both, and this is that rule, applied one node at a time and level-triggered: each
pass reads the present state of a node and does whatever is missing, so a notice that was missed,
repeated or reordered costs nothing. For one node:

- A revoked node is cut off. Its Warden is stopped first, releasing its lease over the link while
  it is still up so the device is left as found; then its link is closed, the listener told to
  stop trusting its key, a Warden that dialled in from the device dropped, and every task that was
  on it taken back to be placed afresh (`swarm.revoked` was recorded by whoever revoked it, so
  that is not recorded here).
- A node a rung change is running for belongs to that flow, and the pass leaves it alone.
- A node on Level 0 that is online with a live link gets a Warden if it has none, and keeps the
  one it has. If the Warden's loop died, or the operator changed the node's access level or tier,
  its tasks are paused and taken back and a Warden is rebuilt, so the Cell it supervises carries
  the new values; for a changed tier the device is first sent `swarm.node_update` on its live link.
  A pending `desired_level` is handed to the colonizer, which carries it out or refuses it.
- A node on Level 1 or above has its Warden on the device, dialled in through the gate's door: it
  is reachable while that Warden's link is open, and its tasks are held when it is not.
- A node that is not reachable is handed to the hold (`hivemind.queen.swarm_gate.unreachable`),
  which marks it, holds its tasks for the grace and then decides.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Run by the gate's loop, once at every tick and whenever the hub, the door or a flow reports a
    change. Calls into `hivemind.queen.cluster` (the pause), `hivemind.queen.swarm_gate` (its own
    modules) and `hivemind.swarm` (the registry, the listener and the node update) only.

Key invariants:
    - One node's failure never stops the pass: it is logged by type and the rest are reconciled.
    - A Warden is rebuilt only after the device has been sent its new terms on the link that is
      up; a send that fails leaves everything as it was for the next pass.
    - The hub's record of a node is refreshed with the registry's before its Warden is rebuilt,
      since `SwarmSource` reads the hub's record first and would otherwise lease on the old terms.
    - The listener is told to re-read the keys once per revocation it sees, not once per pass.
    - A task is never left running on a Warden that was revoked, rebuilt or failed: it is taken
      back, and the decision is on the trail.

See Also:
    - hivemind.swarm.gateway.heartbeat for who flips a node between ONLINE and UNREACHABLE.
    - hivemind.queen.swarm_gate.unreachable for what a lost device's tasks are owed.
    - hivemind.queen.swarm_gate.takeback for how a Warden's tasks are taken back.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import CellError
from hivemind.common.logging import get_logger
from hivemind.queen.cluster import pause_on_warden
from hivemind.queen.swarm_gate.colonize import Colonizer
from hivemind.queen.swarm_gate.colonize.steps import dismiss_remote
from hivemind.queen.swarm_gate.takeback import take_back
from hivemind.queen.swarm_gate.terms import GateContext
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
# Why a Warden's bees are paused before the Warden is rebuilt for new terms.
REBUILD_PAUSE_REASON = "The device's terms changed; its Warden is rebuilt."

log = get_logger(__name__)

__all__ = ["REBUILD_PAUSE_REASON", "TERMS_CHANGED_REASON", "DeviceReconciler", "ReconcileDeps"]


@dataclass(frozen=True, slots=True)
class ReconcileDeps:
    """What the reconciler works with (codingrules 5.1).

    Attributes:
        registry: Who is enrolled, at what level and tier, and who is revoked.
        listener: The swarm listener: its hub says who is connected, and it cuts a device off.
        wardens: The table of Level 0 Wardens being kept in line.
        unreachable: The hold on a lost device's tasks, and its end when the device is back.
        colonizer: Carries out the rung an operator asked for, and says which devices it owns.
    """

    registry: SwarmRegistry
    listener: SwarmListener
    wardens: DeviceWardens
    unreachable: UnreachableDevices
    colonizer: Colonizer


class DeviceReconciler:
    """Make the device Wardens match the registry and the hub, one node at a time."""

    def __init__(self, deps: ReconcileDeps) -> None:
        """Build the reconciler; `bind` gives it the Queen's collaborators.

        Args:
            deps: The registry, listener, Wardens, hold and colonizer it works with.
        """
        self._deps = deps
        self._revoked: set[DeviceId] = set()
        self._context: GateContext | None = None

    def bind(self, context: GateContext) -> None:
        """Take back the tasks of a Warden that goes, from `context`'s Queen, from now on."""
        self._context = context

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
        elif self._deps.colonizer.busy(node.device_id):
            return  # A rung change owns the device until it ends; a pass would get in its way.
        elif node.level is RuntimeLevel.GATEWAY:
            await self._sync_level0(node)
        else:
            await self._sync_colonized(node)

    async def _sync_level0(self, node: SwarmNode) -> None:
        """Serve a Level 0 device while it is reachable, and hold its tasks when it is not."""
        link = self._deps.listener.hub.link(node.device_id)
        # Both: a link that is open but silent is UNREACHABLE in the registry (the monitor marks
        # it), and a node the registry still calls ONLINE after a restart has no link yet.
        if node.status is NodeStatus.ONLINE and link is not None:
            await self._keep(node)
        else:
            await self._deps.unreachable.lost(node)

    async def _sync_colonized(self, node: SwarmNode) -> None:
        """Keep a device whose Warden is on it: reachable while that Warden's link is open."""
        deps = self._deps
        link = deps.wardens.link_of(node.warden_id)
        if link is None or not link.transport.is_connected:
            await deps.unreachable.lost(node)
            return
        await deps.unreachable.found(node)
        if node.desired_level is not None:
            await deps.colonizer.request(node)

    async def _keep(self, node: SwarmNode) -> None:
        """Give a reachable Level 0 device a Warden, keep it, or rebuild it for changed terms."""
        deps = self._deps
        # Idempotent: a device that is reachable has no hold, and one that just returned is freed
        # before its Warden is served again.
        await deps.unreachable.found(node)
        if node.desired_level is not None and await deps.colonizer.request(node):
            return  # A flow now owns the device.
        served = deps.wardens.get(node.device_id)
        if served is not None and served.has_ended:
            await self._retire(node, "failed", polite=False)  # Its loop died of a device error.
            served = None
        if served is None:
            await deps.wardens.serve(node)
        elif _terms_changed(served, node):
            await self._update(node)

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
        await self._retire(node, "rebuilt", polite=True)
        await deps.wardens.serve(node)

    async def _retire(self, node: SwarmNode, cause: str, *, polite: bool) -> None:
        """Stop a Level 0 Warden and take its tasks back, pausing its bees first when it can."""
        deps, context = self._deps, self._context
        if context is not None and polite:
            # Each bee writes a Handoff before its Warden goes, so the task resumes where it was.
            await pause_on_warden(
                context.deps, context.queen.wardens, node.warden_id, REBUILD_PAUSE_REASON
            )
        await deps.wardens.stop(node.device_id)
        if context is not None:
            await take_back(context.deps, node, cause)

    async def _revoke(self, node: SwarmNode) -> None:
        """Cut a revoked device off: stop its Warden, close its link, distrust its key."""
        deps = self._deps
        # The Warden first: it releases its lease over the packet's link, which must still be up
        # for the device to be left as found (docs/waggle/enrolment.md section 11). Closing the
        # link first made that release fail and left the lease to the device's dead-man switch.
        await deps.wardens.stop(node.device_id)
        await deps.listener.disconnect(node.device_id)
        if node.device_id in self._revoked:
            return
        self._revoked.add(node.device_id)
        await deps.listener.sync_keys()
        context = self._context
        if context is None:
            return
        # Once per revocation: a Warden that dialled in from the device is dropped, the hold on
        # its tasks ends with no decision, and its tasks are placed afresh.
        await dismiss_remote(context, deps.wardens, node)
        await deps.unreachable.forget(node)
        await take_back(context.deps, node, "revoked")


def _terms_changed(served: ServedDevice, node: SwarmNode) -> bool:
    """Whether the operator changed the access level or tier the Warden was built with."""
    built = served.node
    return built.access_level is not node.access_level or built.comb_shield is not node.comb_shield
