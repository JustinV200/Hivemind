"""The Queen's side of the Swarm: each device served, held when lost, and moved up the ladder.

Devices are existing machines the Hive borrows as Real Cells and leaves exactly as found; each runs
a Pollen Packet that dials the Hive Stand's swarm listener (`hivemind.swarm`, Layer 3). The listener
admits and routes them but supervises nothing. This package is the part of the Queen that does: it
owns the listener, and serves each device according to its rung of the runtime ladder. At Level 0
the Warden is on the Hive Stand, attached to her, started and running while the device is
connected, told when the device drops and when it returns, rebuilt when the operator changes the
device's terms. Once the operator colonizes the device (`hivemind.queen.swarm_gate.colonize`) its
Warden runs on the device and dials in through the gate's door (`attach`), the same attach path a
Virtual Cell's Warden uses, and it reconnects the same way. A colonized device that declares a model
server and has the room for it can be promoted once more, to a Nuc (`colonize.nuc`), and lowered
back. A device nobody asked to move is judged by the Queen herself (`rungs`): colonized when the
Hive Stand needs relief or a task on it must survive a lost link, given a model server when the
seats are held or a task asks for autonomy, and taken back only after the reasons have been gone for
a time and the device is idle. A device that is lost has its tasks held for a grace, then decided on
by its rung (`unreachable`); a revoked one has them placed afresh (`takeback`). Placed under
`queen`, not `swarm`, because attaching a Warden to the Queen needs the Queen: `hivemind.swarm`
(Layer 3) may never import her (Layer 6), the same reason `hivemind.queen.cell_gate` sits here for
Virtual Cells.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Built by the composition root when
    `[swarm] enabled` is true (`hivemind.cli.compose.swarm`), started with the running `Queen`, run
    beside her, and stopped before her. Calls into `hivemind.cell`, `hivemind.common`,
    `hivemind.honey_store`, `hivemind.memory`, `hivemind.pheromone`, `hivemind.queen.attach`,
    `.cluster`, `.dispatcher`, `.errors`, `.isolation`, `.queen`, `.trail`, `hivemind.swarm`,
    `hivemind.wardens` and waggle only.

Key invariants:
    - A Level 0 device has at most one Warden here, and keeps it across a dropped link; a colonized
      device's Warden is the device's own, attached under the same id.
    - A device's Warden is stopped, releasing its lease over the packet's link, before the listener
      closes that link, so a device is left as found.
    - A hub callback only wakes the gate's loop; every effect (attach, stop, hold, flow) happens
      in the loop or a flow, from the registry and the hub as they are then.
    - A lost device's tasks are held, never failed; a revoked, rebuilt or failed Warden's tasks
      are taken back, never orphaned.
    - A device failing, or its Warden's run loop ending on a device error, never ends the Hive.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the runtime ladder.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the hold and reconnection.
    - hivemind.queen.cell_gate for the equivalent door of Virtual Cells' Wardens.
    - hivemind.cli.compose.swarm for the composition root that builds the gate and its factory.

Public API:
    - SwarmGate: owns the listener, runs the loop, the flows and every Level 0 Warden, and stops
      them in order (gate).
    - GateTerms, GateContext: the numbers the gate works to, and the Queen it works with (terms).
    - DeviceWardenDoor, DoorEvents, GateDoorEvents, link_services: the door a device's own Warden
      dials in through (attach).
    - DeviceWardens, DeviceWarden, DeviceWardenFactory, ServedDevice, STOP_TIMEOUT_S: the table of
      Level 0 Wardens, a built one, the factory that builds it, and one in service (wardens).
    - DeviceReconciler, ReconcileDeps: the rule that keeps the Wardens in line with the registry
      and the hub (reconcile).
    - UnreachableDevices, Diagnosis, NoDiagnosis, HoldState: the hold on a lost device's tasks
      (unreachable).
    - Colonizer, Promotion, Withdrawal, decide_request: moving a Warden onto a device and back
      (colonize).
    - RungEvaluator, RungKeeper, decide_rung: the Queen's own decisions to raise a device a rung or
      take one back, from the Hive's pressure and a task's need to outlive its link (rungs).
    - RegistrySegmentKeys: the key a device's trail segment must be signed with (keys).
    - RegistryUplinkKeys: the key a device Warden's Basket uploads are signed with (keys).
    - take_back, tasks_on, open_tasks: taking a gone Warden's tasks back to be placed afresh,
      and reading the tasks that have not ended (takeback).
    - recall_handoff: the newest Handoff the Queen can hold for a task whose bee wrote it in a
      remote Warden's store (a task taken back, a Warden renewed, a provider resumed), recalled
      from the Honey Store when that store is out of her reach (recall).
"""

from hivemind.queen.swarm_gate.attach import (
    DeviceWardenDoor,
    DoorEvents,
    GateDoorEvents,
    link_services,
)
from hivemind.queen.swarm_gate.colonize import Colonizer, Promotion, Withdrawal, decide_request
from hivemind.queen.swarm_gate.gate import SwarmGate
from hivemind.queen.swarm_gate.keys import RegistrySegmentKeys, RegistryUplinkKeys
from hivemind.queen.swarm_gate.recall import recall_handoff
from hivemind.queen.swarm_gate.reconcile import DeviceReconciler, ReconcileDeps
from hivemind.queen.swarm_gate.rungs import RungEvaluator, RungKeeper, decide_rung
from hivemind.queen.swarm_gate.takeback import open_tasks, take_back, tasks_on
from hivemind.queen.swarm_gate.terms import GateContext, GateTerms
from hivemind.queen.swarm_gate.unreachable import (
    Diagnosis,
    HoldState,
    NoDiagnosis,
    UnreachableDevices,
)
from hivemind.queen.swarm_gate.wardens import (
    STOP_TIMEOUT_S,
    DeviceWarden,
    DeviceWardenFactory,
    DeviceWardens,
    ServedDevice,
)

__all__ = [
    "STOP_TIMEOUT_S",
    "Colonizer",
    "DeviceReconciler",
    "DeviceWarden",
    "DeviceWardenDoor",
    "DeviceWardenFactory",
    "DeviceWardens",
    "Diagnosis",
    "DoorEvents",
    "GateContext",
    "GateDoorEvents",
    "GateTerms",
    "HoldState",
    "NoDiagnosis",
    "Promotion",
    "ReconcileDeps",
    "RegistrySegmentKeys",
    "RegistryUplinkKeys",
    "RungEvaluator",
    "RungKeeper",
    "ServedDevice",
    "SwarmGate",
    "UnreachableDevices",
    "Withdrawal",
    "decide_request",
    "decide_rung",
    "link_services",
    "open_tasks",
    "recall_handoff",
    "take_back",
    "tasks_on",
]
