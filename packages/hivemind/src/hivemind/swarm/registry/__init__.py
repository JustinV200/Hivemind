"""Re-export the swarm registry: the Hive's table of enrolled devices and their invites.

The registry remembers which devices the operator enrolled and which invites are still waiting to
admit one (a device is an existing machine the Hive borrows as a Real Cell, reached through its
Pollen Packet, the thin gateway installed on it). `SwarmRegistry` is the protocol every caller
holds; `SqliteSwarmRegistry` is the durable implementation in the Hive's one database (own
migration series "swarm"); `MemorySwarmRegistry` is the in-memory one for tests and demos. Both
apply the pure rules of `rules` and pass the same contract suite. This file is the package's
face: a caller writes `from hivemind.swarm.registry import SwarmRegistry` without knowing the
split (codingrules 5.2, 5.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm`. Used by the
    enrolment door, the gateway listener and heartbeat monitor, the Queen's swarm gate and the
    `hive swarm` commands. Calls into hivemind.common, hivemind.pheromone and the rest of
    hivemind.swarm.

Key invariants:
    - This file holds re-exports and __all__ only; the modules behind it define every name.
    - Sibling modules inside the package import each other by full module path, never through
      this face.

See Also:
    - docs/waggle/enrolment.md for the invite and the redemption this registry makes atomic.
    - hivemind.cell.leavings for the package-face pattern this one follows.

Public API:
    - SwarmRegistry, InviteRegistry, NodeRegistry, NodeTerms: the protocol and its three parts
      (protocol).
    - SqliteSwarmRegistry, apply_swarm_migrations, SUBSYSTEM, MIGRATIONS_PACKAGE: the durable
      registry and its migration series (sqlite).
    - MemorySwarmRegistry: the in-memory registry for tests and demos (memory).
"""

from hivemind.swarm.registry.memory import MemorySwarmRegistry
from hivemind.swarm.registry.protocol import (
    InviteRegistry,
    NodeRegistry,
    NodeTerms,
    SwarmRegistry,
)
from hivemind.swarm.registry.sqlite import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    SqliteSwarmRegistry,
    apply_swarm_migrations,
)

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "InviteRegistry",
    "MemorySwarmRegistry",
    "NodeRegistry",
    "NodeTerms",
    "SqliteSwarmRegistry",
    "SwarmRegistry",
    "apply_swarm_migrations",
]
