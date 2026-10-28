"""Install the hivemind runtime on a device and run its own Warden: colonization (Level 1).

A Pollen Packet is a gateway at Level 0: the device's Warden runs on the Hive Stand and reaches the
device through the packet. When the Queen decides the device should carry its own Warden (ADR-0052)
she orders it in three steps, one ``swarm.colonize`` each, and this package is how the packet does
them. PREPARE installs the pinned ``hivemind`` version into a virtual environment under the data
directory, from the wheelhouse or index the operator recorded at ``pollen install`` and never from
anything on the wire (``runtime``). START launches ``hivemind-in-cell`` from that environment as a
long-lived background process, in a process group of its own and not part of any lease, with the
device's key file, the listener's URL and pin, its Warden and Cell ids, its access level and tier
and the device manifest in its environment, and records it with the new level, COLONIZED, so the
dead-man switch stands down while the Warden lives (``warden``). WITHDRAW stops it and puts the
level back to GATEWAY. ``ColonizeService`` is the ``DeviceExecutor`` that answers the Queen with
``swarm.colonized`` (``service``); ``WardenKeeper`` adopts a Warden that outlived a restart of the
packet and notices one that died. The packet stays a gateway throughout: it installs files and
starts and stops one process, and it imports nothing from ``hivemind``.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by ``pollen.build`` and
    consulted by ``pollen.agent.dispatch``; its keeper runs as a task of the packet's scope. Calls
    into the platform shim, ``pollen.state``, ``pollen.executors.base`` and waggle only.

Key invariants:
    - The runtime is installed only from the source the operator recorded, and the order pins a
      version, never a place.
    - The level and the Warden record are written together; the dead-man switch is off exactly
      while a Warden is recorded and alive.
    - The packet never starts a second Warden, and never claims one that has died.
    - Nothing here imports ``hivemind``; the environment names it sets are a contract checked by a
      test on the hivemind side.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for the ladder and the three actions.
    - docs/waggle/gateway-contract.md section 17 for what an implementing gateway does.
    - hivemind.cli.in_cell.borrowed for the Warden this starts.

Public API:
    - ColonizeService: the executor that serves ``swarm.colonize`` (service).
    - WardenKeeper, BootstrapTimings, stop_recorded, warden_environment, operator_screen: the
      on-device Warden's process, its waits, stopping a recorded one, the environment it is
      started with, and the operator's screen when the manifest lends it (warden).
    - prepare_runtime, installed_version, entry_point, VERSION_PATTERN, ENTRY_POINT: the
      runtime's environment (runtime).
"""

from pollen.bootstrap.runtime import (
    ENTRY_POINT,
    VERSION_PATTERN,
    entry_point,
    installed_version,
    prepare_runtime,
)
from pollen.bootstrap.service import ColonizeService
from pollen.bootstrap.warden import (
    BootstrapTimings,
    WardenKeeper,
    operator_screen,
    stop_recorded,
    warden_environment,
)

__all__ = [
    "ENTRY_POINT",
    "VERSION_PATTERN",
    "BootstrapTimings",
    "ColonizeService",
    "WardenKeeper",
    "entry_point",
    "installed_version",
    "operator_screen",
    "prepare_runtime",
    "stop_recorded",
    "warden_environment",
]
