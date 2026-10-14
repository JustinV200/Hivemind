"""The Queen's side of the Swarm: a Warden on each enrolled device that is up, kept in line.

Devices are existing machines the Hive borrows as Real Cells and leaves exactly as found; each runs
a Pollen Packet that dials the Hive Stand's swarm listener (`hivemind.swarm`, Layer 3). The listener
admits and routes them but supervises nothing. This package is the part of the Queen that does: it
owns the listener, and keeps one Level 0 Warden per device (a Warden on the Hive Stand whose Cell is
the device, reached through the packet) attached to her, started and running while the device is
connected, told when the device drops and when it returns, stopped when the operator revokes it, and
rebuilt when the operator changes its access level or tier. Placed under `queen`, not `swarm`,
because attaching a Warden to the Queen needs the Queen: `hivemind.swarm` (Layer 3) may never import
her (Layer 6), the same reason `hivemind.queen.cell_gate` sits here for Virtual Cells.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Built by the composition root when
    `[swarm] enabled` is true (`hivemind.cli.compose.swarm`), started with the running `Queen`, run
    beside her, and stopped before her. Calls into `hivemind.cell`, `hivemind.common`,
    `hivemind.queen.attach`, `hivemind.queen.errors`, `hivemind.queen.queen`, `hivemind.swarm`,
    `hivemind.wardens` and waggle only.

Key invariants:
    - A device has at most one Warden here, and keeps it across a dropped link: a device that comes
      back is not served again.
    - A device's Warden is stopped, releasing its lease over the packet's link, before the listener
      closes that link, so a device is left as found.
    - A hub callback only wakes the gate's loop; every effect (attach, stop, alarm) happens in the
      loop, from the registry and the hub as they are then.
    - A device failing, or its Warden's run loop ending on a device error, never ends the Hive.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for Level 0 of the runtime ladder.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for what a lost device is owed
      (the grace and the decision are a later step's; this package raises the first Alarm).
    - hivemind.queen.cell_gate for the equivalent door of Virtual Cells' Wardens.
    - hivemind.cli.compose.swarm for the composition root that builds the gate and its factory.

Public API:
    - SwarmGate: owns the listener, runs the loop and every device Warden, stops them in order
      (gate).
    - DeviceWardens, DeviceWarden, DeviceWardenFactory, ServedDevice, STOP_TIMEOUT_S: the table of
      device Wardens, a built one, the factory that builds it, and one in service (wardens).
    - DeviceReconciler, ReconcileDeps: the rule that keeps the Wardens in line with the registry
      and the hub (reconcile).
    - UnreachableDevices: what a Warden is told when its device's link is lost and back
      (unreachable).
"""

from hivemind.queen.swarm_gate.gate import SwarmGate
from hivemind.queen.swarm_gate.reconcile import DeviceReconciler, ReconcileDeps
from hivemind.queen.swarm_gate.unreachable import UnreachableDevices
from hivemind.queen.swarm_gate.wardens import (
    STOP_TIMEOUT_S,
    DeviceWarden,
    DeviceWardenFactory,
    DeviceWardens,
    ServedDevice,
)

__all__ = [
    "STOP_TIMEOUT_S",
    "DeviceReconciler",
    "DeviceWarden",
    "DeviceWardenFactory",
    "DeviceWardens",
    "ReconcileDeps",
    "ServedDevice",
    "SwarmGate",
    "UnreachableDevices",
]
