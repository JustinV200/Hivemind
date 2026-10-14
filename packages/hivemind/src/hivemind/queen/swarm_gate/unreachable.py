"""Define UnreachableDevices: what the Queen does the moment a Swarm device's link is lost.

A device Warden runs on the Hive Stand, so unlike a Warden that went silent it keeps heartbeating
over memory while its device is away, and the Queen's own liveness check would never notice the
loss. The gate therefore says so itself: when a device is no longer reachable (its link ended, or
its heartbeats stopped), `lost` tells the device's Warden, which raises one `CELL_UNREACHABLE`
Alarm for the loss through the ordinary Alarm path and treats its lease as lost until the device
returns (`Warden.cell_lost`); when the device is reachable again, `found` tells it so, and the next
loss is its own Alarm (`Warden.cell_found`). That is all this step does. Holding the device's tasks
for `[swarm] unreachable_grace_s`, then re-placing or failing them, and diagnosing why the device
went away, are the next step's (ADR-0053); they extend this class.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.swarm_gate`.
    Called by the gate's reconcile pass and by the gate itself, which notes why a device went.
    Calls into `hivemind.queen.swarm_gate.wardens` and `hivemind.swarm` only.

Key invariants:
    - The Warden decides whether a loss is new (it raises one Alarm per loss however often it is
      told), so telling it again on every reconcile pass is safe and is how a missed notice heals.
    - A device with no Warden has nothing to tell: `lost` and `found` do nothing for it.
    - Why a device went is a short stable word the hub gave (`closed`, `missed_heartbeats`); it is
      never an address, a key or a path.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the grace this will add.
    - hivemind.wardens.warden.Warden.cell_lost for the Alarm and the lease it treats as lost.
"""

from __future__ import annotations

from hivemind.queen.swarm_gate.wardens import DeviceWardens
from hivemind.swarm import SwarmNode
from waggle.ids import DeviceId

# What an Alarm says when the hub gave no reason, e.g. a device the reconcile pass finds with no
# live link after a notice was missed.
NO_LINK_REASON = "no_live_link"

__all__ = ["NO_LINK_REASON", "UnreachableDevices"]


class UnreachableDevices:
    """Tell a device's Warden when its link is lost, and when it is back."""

    def __init__(self, wardens: DeviceWardens) -> None:
        """Build the handler over the table of device Wardens.

        Args:
            wardens: Where a device's Warden is found.
        """
        self._wardens = wardens
        self._reasons: dict[DeviceId, str] = {}

    def note(self, device_id: DeviceId, reason: str) -> None:
        """Remember why a device just went away, for the Alarm its Warden will raise.

        Args:
            device_id: The device.
            reason: The hub's short reason for the loss.
        """
        self._reasons[device_id] = reason

    async def lost(self, node: SwarmNode) -> None:
        """Tell the device's Warden its link is lost; it alarms once per loss.

        Args:
            node: The device that is not reachable.
        """
        served = self._wardens.get(node.device_id)
        if served is None:
            return
        reason = self._reasons.get(node.device_id, NO_LINK_REASON)
        await served.warden.cell_lost(reason)

    def found(self, node: SwarmNode) -> None:
        """Tell the device's Warden its link is back, and forget why it went.

        Args:
            node: The device that is reachable again.
        """
        self._reasons.pop(node.device_id, None)
        served = self._wardens.get(node.device_id)
        if served is not None:
            served.warden.cell_found()
