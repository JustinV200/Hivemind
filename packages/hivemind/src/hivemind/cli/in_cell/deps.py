"""Compose WardenDeps for the in-Cell Warden: the manifest-free counterpart of cli.compose.deps.

`hivemind.cli.compose.deps.build_warden_deps` builds the Hive Stand's own `WardenDeps` from a
loaded Hive Manifest (codingrules section 13); a Virtual Cell boots from `HIVEMIND_*` environment
variables alone (`hivemind.manifest.env.InCellEnv`), so this module is the same conversion with no
manifest to read from. It reaches for the same shipped defaults `build_warden_deps` falls back to
when an operator has not overridden them (`hivemind.supervision.load_warden_policy(None)`,
`hivemind.supervision.capping.load_tiers(None)`, `hivemind.guard.load_guard_policy()`), since a
Virtual Cell image carries no `[supervision]` or `[guard]` section to name an override with in the
first place. Roadmap step 10.3: the Warden's Guard `Enforcer` is built over that same shipped policy
and records to this Cell's own trail segment (shipped to the Queen like every other row), and its
lease needs `cell:virtual` -- set here, because this module is the one place that knows it built a
Virtual Cell's source, never read off the Cell's kind. Roadmap step 10.3a: the policy names the
Hive Stand as this Cell reaches it (its Queen URL's host as a name, and the addresses that host
resolved to at start, `hivemind.cli.in_cell.hive_stand`), so the Hive-state floor refuses a Worker
`net` to it, and the deps say which providers this Cell serves locally, for Night Veil's
local-only binding rule. Every model call in the Cell passes through the Cell's own Fanner
(`hivemind.cli.in_cell.fanner`, the seat meter codingrules section 8.10 requires of every call):
the Warden's own awake episodes on one unattributed lane (`call_gate`), each sub-bee on its own
lane for its grant and goal (`lane_for_grant`), and each judge review on a lane of its own tempo,
all recording `llm.call` to this Cell's own trail segment, which the trail sync ships to the Queen.
The Exoskeleton wiring (roadmap steps 6.4-6.6) comes from
`hivemind.cli.compose.exoskeleton.in_cell_exoskeleton`: the default screen, Chromium without its
own sandbox (the Cell is the sandbox), a browser launcher only where the browser extra is
installed, unmetered ears when the Cell's slot table binds a transcriber, and an in-memory
recording store. A Virtual Cell has no database file of its own, so its flight recordings live
with the Cell, in this process, and are gone when the Cell is torn down; shipping them to the
Queen's store is a later step.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell` (CellIdentity), `hivemind.forage.slots` (ModelSlot), `hivemind.guard`
    (Capability, CapabilityFamily, Enforcer, load_guard_policy), `hivemind.forage` (Tempo),
    `hivemind.memory` (InMemoryMemoryStore, MemoryIdentity), `hivemind.pheromone` (PheromoneTrail),
    `hivemind.llm` (Fanner, the slot errors), `hivemind.supervision` (load_warden_policy),
    `hivemind.supervision.capping` (deterministic_checks, judge_checks, load_tiers, the judge
    rubrics), `hivemind.wardens` (WardenDeps, ModelJudgeReviewer),
    `hivemind.wardens.snapshot_relay` (RelaySnapshotter), `hivemind.wardens.spawn`
    (InCellSpawnSource),
    `hivemind.wardens.trail_sync` (TrailSyncDeps, WaggleTrailSync), `hivemind.workers.roles`
    (worker_for), `hivemind.cli.in_cell.providers`, `hivemind.cli.in_cell.fanner`,
    `hivemind.cli.in_cell.hive_stand`, `hivemind.cli.compose.exoskeleton` (in_cell_exoskeleton)
    and waggle only.

Key invariants:
    - `worker_factory` is `hivemind.workers.roles.worker_for` (roadmap step 6.9), the same mapping
      `hivemind.cli.compose.deps.build_warden_deps` wires for the Hive Stand.
    - The gate has a JUDGE rung, and the Warden a model-backed judge, exactly when the Cell's slot
      table binds `ModelSlot.JUDGE`; without one, every JUDGE tier fails closed.
    - `call_gate`, every `lane_for_grant` lane and every judge review's lane are lanes of one
      Fanner per Cell, built the same way whichever provider registry was built (the fallback fake
      one included): no model call in a Virtual Cell ever bypasses the seat meter or goes
      unrecorded.

See Also:
    - .claude/codingrules.md section 5.1 for "introduce a frozen dataclass for the argument group",
      the reason `WardenDeps` itself takes this many fields.
    - hivemind.cli.compose.deps for build_warden_deps, the manifest-driven sibling this mirrors.
    - hivemind.cli.in_cell.providers for build_in_cell_provider_registry, this module's model door.
    - hivemind.cli.in_cell.fanner for build_in_cell_fanner/lane_for_grant, its seat meter.
    - hivemind.wardens.deps for WardenDeps, this module's one return type.
"""

from __future__ import annotations

import dataclasses
from urllib.parse import urlsplit

from hivemind.cell import CellIdentity
from hivemind.cli.compose.exoskeleton import in_cell_exoskeleton
from hivemind.cli.in_cell.config import InCellRuntimeConfig
from hivemind.cli.in_cell.fanner import build_in_cell_fanner, lane_for_grant
from hivemind.cli.in_cell.hive_stand import hive_stand_names
from hivemind.cli.in_cell.providers import build_in_cell_provider_registry, local_provider_names
from hivemind.common.logging import get_logger
from hivemind.forage import Tempo
from hivemind.forage.slots import ModelSlot
from hivemind.guard import Capability, CapabilityFamily, Enforcer, load_guard_policy
from hivemind.guard.net import ip_literal
from hivemind.guard.policy import HiveState
from hivemind.llm import Fanner, ProviderRegistry, UnknownProviderError, UnresolvableSlotError
from hivemind.memory import InMemoryMemoryStore, MemoryIdentity
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision import load_warden_policy
from hivemind.supervision.capping import deterministic_checks, judge_checks, load_tiers
from hivemind.supervision.capping.checks.rubrics import load_judge_rubrics
from hivemind.wardens import ModelJudgeReviewer
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.snapshot_relay import RelaySnapshotter
from hivemind.wardens.spawn import InCellSpawnSource
from hivemind.wardens.trail_sync import TrailSyncDeps, WaggleTrailSync
from hivemind.workers.roles import worker_for
from waggle.clock import Clock
from waggle.envelope import Hop
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

log = get_logger(__name__)

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
    fanner = build_in_cell_fanner(config, trail, clock)  # One per Cell, whichever registry.
    enforcer = _build_enforcer(config, trail, clock)  # Its policy is also every set's (`guard`).
    deps = WardenDeps(
        source=source,
        queen_link=queen_link,
        hop=_hop(config),
        memory=InMemoryMemoryStore(trail),
        trail=trail,
        identity=_identity(config),
        clock=clock,
        policy=load_warden_policy(None),  # No [supervision] section inside a Cell: the shipped one.
        tiers=load_tiers(None),  # Same reasoning: the shipped capping-tiers.toml.
        guard=enforcer.policy,
        enforcer=enforcer,  # Roadmap step 10.3.
        lease_capability=VIRTUAL_CELL_LEASE,
        bindings=config.slots,  # What a named binding resolves against at slot_binding.
        local_providers=local_provider_names(config),  # Roadmap step 10.3a.
        checks=deterministic_checks(),  # The JUDGE rung joins below, when a judge is bound.
        bound=registry.bound(ModelSlot.WARDEN),
        call_gate=fanner.lane(Tempo()),  # The Warden's own lane, for its awake episodes.
        worker_factory=worker_for,
        rebind=lambda key: registry.bound_for_key(key, ModelSlot.WORKER),
        handoff_threshold=DEFAULT_HANDOFF_THRESHOLD,
        heartbeat_interval_s=config.heartbeat_interval_s,
        worker_heartbeat_interval_s=DEFAULT_WORKER_HEARTBEAT_INTERVAL_S,
        missed_heartbeats_before_stalled=DEFAULT_MISSED_HEARTBEATS_BEFORE_STALLED,
        lane_for_grant=lane_for_grant(fanner),  # One lane per sub-bee's grant and goal.
    )
    deps = _with_queen_relays(deps, config, queen_link, trail, clock)
    return _equipped(deps, registry, fanner, clock)


def _with_queen_relays(
    deps: WardenDeps,
    config: InCellRuntimeConfig,
    queen_link: Transport,
    trail: PheromoneTrail,
    clock: Clock,
) -> WardenDeps:
    """Give this Warden the two things it can only do through the Queen: ship its trail, snapshot.

    Split out of `build_in_cell_warden_deps` for its line budget (codingrules 5.1).
    """
    return dataclasses.replace(
        deps,
        trail_sync=_build_trail_sync(config, queen_link, trail, clock),
        snapshotter=_build_snapshotter(config, queen_link, _hop(config), clock),
    )


def _equipped(
    deps: WardenDeps, registry: ProviderRegistry, fanner: Fanner, clock: Clock
) -> WardenDeps:
    """Give this Warden its Exoskeleton wiring and, when the slot table binds one, its judge."""
    # Roadmap steps 6.4-6.6: the Exoskeleton's screen, launcher, recorder and ears (module docs).
    return in_cell_exoskeleton(registry, clock).apply(_with_judge(deps, registry, fanner))


def _with_judge(deps: WardenDeps, registry: ProviderRegistry, fanner: Fanner) -> WardenDeps:
    """Give this Cell's gate the model-backed judge its slot table binds, as the Hive Stand's has.

    Without one, every tier whose check ladder includes JUDGE (irreversible, device_command,
    outside_scratch_write, spend) fails closed at the gate, so an irreversible GUI action on a
    desktop Cell could never land, and every audit sampled the default, unscripted fake judge.
    A table that binds no judge keeps exactly that fail-closed behaviour, and says so once. Each
    review runs on a lane of the Cell's own Fanner, on the proposal's tempo, like the Hive Stand's.
    """
    try:
        # Resolving a binding is bookkeeping only: no provider is contacted until a review runs.
        bound = registry.bound(ModelSlot.JUDGE)
    except (UnresolvableSlotError, UnknownProviderError) as error:
        log.warning("in_cell.judge_unbound", reason=type(error).__name__)
        return deps
    reviewer = ModelJudgeReviewer(bound=bound, lane_for=fanner.lane)
    rubrics = load_judge_rubrics()
    checks = {**deps.checks, **judge_checks(reviewer, rubrics)}
    return dataclasses.replace(deps, checks=checks, judge_reviewer=reviewer, judge_rubrics=rubrics)


def _hop(config: InCellRuntimeConfig) -> Hop:
    """Return this Warden's own address toward the Queen: itself, to her Hive, from this node."""
    return Hop(sender=config.warden_id, recipient=config.hive_id, node_id=config.node_id)


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
    policy = dataclasses.replace(load_guard_policy(), hive_state=_hive_stand_state(config))
    return Enforcer(policy, trail, clock, identity)


def _hive_stand_state(config: InCellRuntimeConfig) -> HiveState:
    """Name the Hive Stand as this Cell reaches it: its host's name and its addresses.

    The Hive's files live on the Hive Stand, not in this Cell, so only the Hive Stand's names and
    addresses apply here: the Queen URL's host (a gateway alias, an onion service: refused by
    name) and what that host resolved to at start, or the host itself when it is an address.
    """
    host = urlsplit(config.queen_waggle_url).hostname or ""
    literal = ip_literal(host)
    own = (*config.hive_stand_addresses, *((literal,) if literal is not None else ()))
    return HiveState.of(own_addresses=own, own_host_names=hive_stand_names(config))


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
