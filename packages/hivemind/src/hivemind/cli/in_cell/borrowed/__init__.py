"""Run the in-Cell Warden on a Swarm device the Hive borrows: borrowed mode (roadmap step 11.6a).

A device that has been colonized (Level 1 of the runtime ladder, ADR-0052) runs its own Warden: the
Warden the Hive Stand ran for it at Level 0, now on the device, with the same id, signing as the
device's node, leasing the machine through the lease machinery the Hive Stand uses, and keeping its
memory and trail in SQLite files under a state directory so that a Handoff outlives a restart. The
Pollen Packet starts it as `hivemind-in-cell` with `HIVEMIND_CELL_LEASE=borrowed` and the
environment `config` reads; this package is what `hivemind.cli.in_cell.main` runs then. `config`
validates that environment and the device manifest (`[llm]` and the `[forage.map]` rows its servers
load; `llm` converts them into the provider, slot and server tables); `stores` opens the durable
stores; `dial` is the dialer and announcer the reconnecting link uses on each connection (it
announces the level and tier the Queen granted, and the model servers the manifest declares, not
started); `deps` builds the link and the Warden's deps from the core a Virtual Cell's Warden
shares; `run` connects and runs the Warden until a signal or a Queen-sent Shutdown stops it,
leaving the device as it was found. A Virtual Cell's Warden (the default, `owned` mode) is
untouched by any of this.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), a sub-package of `hivemind.cli.in_cell`, which
    selects it in `run_in_cell_warden` when the environment says `borrowed`. Calls into
    `hivemind.cell`, `hivemind.cli.in_cell`, `hivemind.manifest`, `hivemind.memory`,
    `hivemind.pheromone`, `hivemind.wardens` and waggle only.

Key invariants:
    - Nothing here reads `os.environ`; it is given the already-read `InCellEnv` (codingrules 13).
    - The Warden keeps its id, and signs as the device's node with the key the packet holds.
    - The device holds the MEADOW tier only; any other is refused at start.
    - No Virtual Cell extra is built for a device: no snapshotter, resident Basket or Exoskeleton
      wiring. A model server the manifest declares is known to the Warden but never started at
      its start: only the Queen's promotion of the device to a Nuc starts one.

See Also:
    - docs/adr/0052-runtime-ladder-warden-moves-first.md and 0053 (offline operation).
    - .claude/phase-11-handoff.md section 2.7 for this mode in the CLI map.
    - hivemind.wardens.offline for the reconnecting link and the Outage this wires in.

Public API:
    - run_borrowed_warden, stop_on_signals: run the Warden, and the signals that stop it (run).
    - BorrowedConfig, build_borrowed_config, is_borrowed, BORROWED, OWNED: the configuration and the
      lease-mode switch (config).
    - DeviceLlm, device_llm: the device manifest's model tables (llm).
    - DeviceStores, open_device_stores: the durable stores (stores).
    - build_device_link, build_borrowed_warden_deps, BorrowedParts: the composition (deps).
    - DeviceDialer, DeviceAnnouncer, DeviceIdentity, LateTrailSync: the link's collaborators (dial).
"""

from hivemind.cli.in_cell.borrowed.config import (
    BORROWED,
    OWNED,
    BorrowedConfig,
    build_borrowed_config,
    is_borrowed,
)
from hivemind.cli.in_cell.borrowed.deps import (
    BorrowedParts,
    build_borrowed_warden_deps,
    build_device_link,
)
from hivemind.cli.in_cell.borrowed.dial import (
    DeviceAnnouncer,
    DeviceDialer,
    DeviceIdentity,
    LateTrailSync,
)
from hivemind.cli.in_cell.borrowed.llm import DeviceLlm, device_llm
from hivemind.cli.in_cell.borrowed.run import run_borrowed_warden, stop_on_signals
from hivemind.cli.in_cell.borrowed.stores import DeviceStores, open_device_stores

__all__ = [
    "BORROWED",
    "OWNED",
    "BorrowedConfig",
    "BorrowedParts",
    "DeviceAnnouncer",
    "DeviceDialer",
    "DeviceIdentity",
    "DeviceLlm",
    "DeviceStores",
    "LateTrailSync",
    "build_borrowed_config",
    "build_borrowed_warden_deps",
    "build_device_link",
    "device_llm",
    "is_borrowed",
    "open_device_stores",
    "run_borrowed_warden",
    "stop_on_signals",
]
