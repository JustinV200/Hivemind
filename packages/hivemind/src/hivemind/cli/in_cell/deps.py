"""Compose WardenDeps for the in-Cell Warden: the manifest-free counterpart of cli.compose.deps.

`hivemind.cli.compose.deps.build_warden_deps` builds the Hive Stand's own `WardenDeps` from a
loaded Hive Manifest (codingrules section 13); a Virtual Cell boots from `HIVEMIND_*` environment
variables alone (`hivemind.manifest.env.InCellEnv`), so this module is the same conversion with no
manifest to read from. It reaches for the same shipped defaults `build_warden_deps` falls back to
when an operator has not overridden them (`hivemind.supervision.load_policy(None)`,
`hivemind.supervision.capping.load_tiers(None)`, `hivemind.guard.load_guard_policy()`), since a
Virtual Cell image carries no `[supervision]` or `[guard]` section to name an override with in the
first place. Roadmap step 10.3: the Warden's Guard `Enforcer` is built over that same shipped policy
and records to this Cell's own trail segment (shipped to the Queen like every other row), and its
lease needs `cell:virtual` -- set here, because this module is the one place that knows it built a
Virtual Cell's source, never read off the Cell's kind.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell` (CellIdentity), `hivemind.forage.slots` (ModelSlot), `hivemind.guard`
    (Capability, CapabilityFamily, Enforcer, load_guard_policy),
    `hivemind.llm.ladders.gate` (DirectCallGate),
    `hivemind.memory` (InMemoryMemoryStore, MemoryIdentity), `hivemind.pheromone` (PheromoneTrail),
    `hivemind.supervision` (load_policy), `hivemind.supervision.capping` (deterministic_checks,
    load_tiers), `hivemind.wardens` (WardenDeps), `hivemind.wardens.snapshot_relay`
    (RelaySnapshotter), `hivemind.wardens.spawn` (InCellSpawnSource),
    `hivemind.wardens.trail_sync` (TrailSyncDeps, WaggleTrailSync), `hivemind.workers.roles`
    (Drone), `hivemind.cli.in_cell.providers` and waggle only.

Key invariants:
    - `worker_factory` always returns a fresh `Drone`, the same "v0's only Worker role" choice
      `hivemind.cli.compose.deps.build_warden_deps` makes for the Hive Stand.
    - `call_gate`/`lane_for_grant` are both unmetered (`DirectCallGate`): a single Virtual Cell has
      no Fanner of its own to share a seat meter across bees the way the Hive Stand's shared pool
      does (roadmap step 5.5 scope; a per-Cell Fanner is a later step, flagged in this dispatch's
      report).

See Also:
    - .claude/codingrules.md section 5.1 for "introduce a frozen dataclass for the argument group",
      the reason `WardenDeps` itself takes this many fields.
    - hivemind.cli.compose.deps for build_warden_deps, the manifest-driven sibling this mirrors.
    - hivemind.cli.in_cell.providers for build_in_cell_provider_registry, this module's model door.
    - hivemind.wardens.deps for WardenDeps, this module's one return type.
"""

from __future__ import annotations

from hivemind.cell import CellIdentity
from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.cli.in_cell.providers import build_in_cell_provider_registry
from hivemind.forage.slots import ModelSlot
from hivemind.guard import Capability, CapabilityFamily, Enforcer, load_guard_policy
from hivemind.llm.ladders.gate import DirectCallGate
from hivemind.memory import InMemoryMemoryStore, MemoryIdentity
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision import load_policy
from hivemind.supervision.capping import deterministic_checks, load_tiers
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.snapshot_relay import RelaySnapshotter
from hivemind.wardens.spawn import InCellSpawnSource
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from hivemind.workers import Worker
from hivemind.workers.roles import Drone
from waggle.clock import Clock
from waggle.envelope import Hop
from waggle.messages.task import WorkerRole
from waggle.transport.base import Transport

# This Warden's own Heartbeat cadence toward the Queen, and each sub-bee's own toward it: no
# manifest [supervision] section exists inside a Virtual Cell to read a cadence from, so this
# reuses the same interval CellReady/CapacityReport/CellHeartbeat announce with
# (hivemind.cli.in_cell.config.DEFAULT_HEARTBEAT_INTERVAL_S), one fixed cadence for every liveness
# signal this Cell sends until a future step threads a configured one through (that module's own
# docstring makes the same call for CellHeartbeat).
DEFAULT_WORKER_HEARTBEAT_INTERVAL_S = 15.0
# How many of a sub-bee's own heartbeat intervals may pass with nothing heard before this Warden
# raises AlarmKind.WORKER_STALLED; generous enough that one missed beat from GC pause or a slow
# tool call is not mistaken for a stall.
DEFAULT_MISSED_HEARTBEATS_BEFORE_STALLED = 3
# codingrules section 8.9: "two thirds of its window" is the documented default threshold.
DEFAULT_HANDOFF_THRESHOLD = 2.0 / 3.0
# Roadmap step 10.3: what this Cell's own Warden's lease needs of its set (lease_creation).
VIRTUAL_CELL_LEASE = Capability(family=CapabilityFamily.CELL_VIRTUAL)

__all__ = [
    "DEFAULT_HANDOFF_THRESHOLD",
    "DEFAULT_MISSED_HEARTBEATS_BEFORE_STALLED",
    "DEFAULT_WORKER_HEARTBEAT_INTERVAL_S",
    "VIRTUAL_CELL_LEASE",
    "build_in_cell_warden_deps",
]


def build_in_cell_warden_deps(
    config: InCellRuntimeConfig,
    source: InCellSpawnSource,
    queen_link: Transport,
    trail: PheromoneTrail,
    clock: Clock,
) -> WardenDeps:
    """Build this Cell's own WardenDeps: manifest-free, from InCellRuntimeConfig and its own deps.

    Args:
        config: This process's own validated runtime config (`config.build_runtime_config`).
        source: This Cell's own one-Cell `InCellSpawnSource`, built from `config.spawn_config`.
        queen_link: The signed WebSocket transport this Cell already announced CellReady over.
        trail: This Cell's own local Pheromone Trail segment.
        clock: Injected time source shared by every collaborator this composes.

    Returns:
        A WardenDeps ready for `hivemind.wardens.Warden(config.warden_id, deps)`.
    """
    registry = build_in_cell_provider_registry(clock, config)
    hop = Hop(sender=config.warden_id, recipient=config.hive_id, node_id=config.node_id)
    enforcer = _build_enforcer(config, trail, clock)  # Its policy is also every set's (`guard`).
    return WardenDeps(
        source=source,
        queen_link=queen_link,
        hop=hop,
        memory=InMemoryMemoryStore(trail),
        trail=trail,
        identity=_identity(config),
        clock=clock,
        policy=load_policy(None),  # No [supervision] section inside a Cell: the shipped default.
        tiers=load_tiers(None),  # Same reasoning: the shipped capping-tiers.toml.
        guard=enforcer.policy,
        enforcer=enforcer,  # Roadmap step 10.3.
        lease_capability=VIRTUAL_CELL_LEASE,
        bindings=config.slots,  # What a named binding resolves against at slot_binding.
        checks=deterministic_checks(),  # No JudgeReviewer wired yet; see this dispatch's report.
        bound=registry.bound(ModelSlot.WARDEN),
        call_gate=DirectCallGate(),  # No per-Cell Fanner yet (module docstring's own note).
        worker_factory=_build_drone,
        rebind=lambda key: registry.bound_for_key(key, ModelSlot.WORKER),
        handoff_threshold=DEFAULT_HANDOFF_THRESHOLD,
        heartbeat_interval_s=config.heartbeat_interval_s,
        worker_heartbeat_interval_s=DEFAULT_WORKER_HEARTBEAT_INTERVAL_S,
        missed_heartbeats_before_stalled=DEFAULT_MISSED_HEARTBEATS_BEFORE_STALLED,
        trail_sync=_build_trail_sync(config, queen_link, trail, clock),
        snapshotter=_build_snapshotter(config, queen_link, hop, clock),
    )


def _identity(config: InCellRuntimeConfig) -> MemoryIdentity:
    """Return the identity this Warden stamps its memory writes with: itself, on this node."""
    return MemoryIdentity(
        hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
    )


def _build_enforcer(config: InCellRuntimeConfig, trail: PheromoneTrail, clock: Clock) -> Enforcer:
    """Build this Warden's Guard Enforcer over the shipped policy, recording as the Warden itself.

    No `[guard]` section exists inside a Cell, so the policy is the one `hivemind.guard.defaults`
    ships (roadmap step 10.3); the refusals land on this Cell's own trail segment, shipped to the
    Queen like every other row.
    """
    identity = CellIdentity(
        hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
    )
    return Enforcer(load_guard_policy(), trail, clock, identity)


def _build_snapshotter(
    config: InCellRuntimeConfig, queen_link: Transport, hop: Hop, clock: Clock
) -> RelaySnapshotter:
    """Build the RelaySnapshotter that asks the Queen to snapshot/roll back this Cell.

    This Cell's own Warden cannot reach the host's Docker daemon or QEMU process (ADR-0027): ask
    the Queen over the same queen_link instead (roadmap step 5.10's own follow-up gap).
    """
    return RelaySnapshotter(config.spawn_config.cell_id, queen_link, hop, clock)


def _build_trail_sync(
    config: InCellRuntimeConfig, queen_link: Transport, trail: PheromoneTrail, clock: Clock
) -> WaggleTrailSync:
    """Build the WaggleTrailSync that ships this Cell's own trail segment to the Queen.

    This Cell's own store dies with the container (`WardenDeps.trail_sync`'s own docstring):
    ship its segment to the Queen on this Warden's heartbeat cadence and once more on stop.
    """
    return WaggleTrailSync(
        TrailSyncDeps(
            trail=trail,
            transport=queen_link,
            node_id=config.node_id,
            cell_id=config.spawn_config.cell_id,
            warden_id=config.warden_id,
            hive_id=config.hive_id,
            clock=clock,
        )
    )


def _build_drone(role: WorkerRole) -> Worker:
    """Return a fresh Drone for every `TaskAssign.role`.

    v0's only Worker role (module docstring).
    """
    return Drone()
