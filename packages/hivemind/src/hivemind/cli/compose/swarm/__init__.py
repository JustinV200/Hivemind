"""Compose the Swarm: the listener devices dial, the door that admits them, and a Warden per device.

The Swarm is the mesh of existing machines the Hive borrows as Real Cells (each carrying a Pollen
Packet that dials the Hive Stand) and leaves exactly as found. Every conversion from `[swarm]` and
the Hive's shared parts into the Swarm's running pieces happens here and nowhere else (codingrules
section 13): `build_swarm` opens the registry, mints the invite door, builds the listener and the
Queen's swarm gate, and `SwarmWardenFactory` makes the Level 0 Warden the gate gives each device,
with its lease-scoped command guard (`lease_scope`). With `[swarm] enabled = false` nothing is
built. `door` builds the enrolment door `hive swarm invite` mints with, without a whole Hive: the
invite's address half is shared with the running Hive's, so the two cannot disagree.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive` (through `_assemble_hive`), whose `Hive.swarm` holds the
    result for `run_hive` to start, and by `hive swarm invite` (`door`). Calls into `hivemind.cell`,
    `hivemind.cli.compose.deps` and `.links`, `hivemind.cli.stores`, `hivemind.common.secrets`,
    `hivemind.guard`, `hivemind.queen`, `hivemind.swarm`, `hivemind.wardens` and waggle only.

Key invariants:
    - A Hive with `[swarm] enabled = false` has no `SwarmAccess`, opens no registry, mints no key
      and binds no port.
    - A device's Warden is made from the part of the Hive Stand's bag any Real Cell shares, so it
      holds no handle on this machine's own disk, display or models.
    - Nothing here is started: `hivemind.cli.compose.hive.run_hive` starts the gate with the Queen.

See Also:
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the trust model.
    - hivemind.queen.swarm_gate for the gate and the table of device Wardens this wires up.
    - hivemind.cli.compose.virtual_cells for the other optional subsystem composed the same way.

Public API:
    - SwarmAccess, build_swarm: the Swarm's handles and the builder that makes them, or None when
      the Swarm is off (build).
    - SwarmWardenFactory: the Level 0 Warden of one enrolled device (device_warden).
    - LeaseScopedGuard, LeaseScopedSource: a device Warden's command guard, per lease
      (lease_scope).
    - build_invite_door, invite_endpoint: the door an invite is minted with, and the address half
      every invite carries (door).
"""

from hivemind.cli.compose.swarm.build import SwarmAccess, build_swarm
from hivemind.cli.compose.swarm.device_warden import SwarmWardenFactory
from hivemind.cli.compose.swarm.door import build_invite_door, invite_endpoint
from hivemind.cli.compose.swarm.lease_scope import LeaseScopedGuard, LeaseScopedSource

__all__ = [
    "LeaseScopedGuard",
    "LeaseScopedSource",
    "SwarmAccess",
    "SwarmWardenFactory",
    "build_invite_door",
    "build_swarm",
    "invite_endpoint",
]
