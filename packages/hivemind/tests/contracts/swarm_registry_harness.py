"""Share the registry contract's rig: a registry, the trail its events land on, and a clock.

The SwarmRegistry contract is two modules (`test_swarm_registry_contract.py` for invites, nodes
and their writes, `test_swarm_registry_ladder_contract.py` for the runtime ladder) because one
file would pass codingrules 5.1's test limit; this is the rig both run over, so the two cannot
drift on how a registry is built. It builds the registry of the parametrised kind over a trail of
the same kind: `MemorySwarmRegistry` over `MemoryPheromoneTrail`, and `SqliteSwarmRegistry` over
`SqlitePheromoneTrail` on two connections to one tmp_path file (ADR-0006 decision 1).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Imported by the two registry contract modules.

Key invariants:
    - The trail's migration runs before the registry's own on SQLite, since the registry's events
      are written into the trail's table.
    - Both kinds share one FakeClock, so a time a test advances reads the same on both.

See Also:
    - hivemind.swarm.registry.protocol for the protocol under contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from builders.cells import make_identity
from builders.swarm import make_device_party, make_swarm_invite, make_swarm_node

from hivemind.common.sqlite import connect
from hivemind.pheromone import (
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    SwarmEvent,
    TrailQuery,
)
from hivemind.swarm.events import SwarmRecorder
from hivemind.swarm.models import SwarmInvite, SwarmNode
from hivemind.swarm.registry import MemorySwarmRegistry, SqliteSwarmRegistry, SwarmRegistry
from waggle.clock import FakeClock
from waggle.ids import new_device_id

__all__ = ["KINDS", "Rig", "open_rig"]

KINDS = ("memory", "sqlite")  # The implementations that ship; a new one joins here.


@dataclass(frozen=True, slots=True)
class Rig:
    """A registry, the trail its events land on and the clock they are stamped with."""

    registry: SwarmRegistry
    trail: PheromoneTrail
    recorder: SwarmRecorder
    clock: FakeClock

    def event(self, kind: str = "swarm.online", subject: str | None = None) -> SwarmEvent:
        """A fresh, valid swarm event about `subject` (a new device when omitted)."""
        about = subject if subject is not None else new_device_id(self.clock)
        return self.recorder.swarm_event(kind, about, {"reason": "test"})

    async def kinds(self) -> list[str]:
        """The kind of every event on the trail, oldest first."""
        return [e.kind for e in await self.trail.query(TrailQuery())]

    async def enrol(self, **node_changes: object) -> SwarmNode:
        """Enrol a fresh ONLINE node through a fresh invite; return the stored node."""
        invite = make_swarm_invite(clock=self.clock)
        await self.registry.put_invite(invite)
        node = make_swarm_node(make_device_party(self.clock), self.clock, **node_changes)
        return await self.registry.redeem(invite.code_hash, node, self.clock.now())

    async def issue(self, **invite_changes: object) -> SwarmInvite:
        """Store a fresh ISSUED invite; return it."""
        invite = make_swarm_invite(None, self.clock, **invite_changes)
        await self.registry.put_invite(invite)
        return invite


async def open_rig(kind: str, tmp_path: Path) -> Rig:
    """Build a registry of `kind` over a trail of the same kind, sharing one clock.

    Args:
        kind: One of `KINDS`.
        tmp_path: A directory for the SQLite file.

    Returns:
        The rig.
    """
    clock = FakeClock()
    identity = make_identity(clock)
    if kind == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return Rig(
            MemorySwarmRegistry(memory_trail),
            memory_trail,
            SwarmRecorder(memory_trail, clock, identity),
            clock,
        )
    # SQLite: two connections to the same file; the trail's migration must run before the
    # registry's own, whose events are written into the trail's table.
    path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(path), clock)
    registry = await SqliteSwarmRegistry.create(connect(path), clock)
    return Rig(registry, sqlite_trail, SwarmRecorder(sqlite_trail, clock, identity), clock)
