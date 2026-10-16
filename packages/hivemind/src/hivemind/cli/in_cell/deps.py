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
recording store. The Cell's own resident Basket (roadmap phase 9) is a small store below its
scratch root (`hivemind.cli.in_cell.basket`); the Warden is also given that store and the keys its
link already holds, to sign and verify the blobs it ships. A Virtual Cell has no database file of
its own, so its flight recordings live with the Cell, in this process, and are gone when the Cell
is torn down; shipping them to the Queen's store is a later step.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell`. Calls into
    `hivemind.cell` (CellIdentity, RealCellSource), `hivemind.forage.slots` (ModelSlot),
    `hivemind.guard`
    (Capability, CapabilityFamily, Enforcer, load_guard_policy), `hivemind.forage` (Tempo),
    `hivemind.memory` (InMemoryMemoryStore, MemoryIdentity), `hivemind.pheromone` (PheromoneTrail),
    `hivemind.llm` (Fanner, the slot errors), `hivemind.supervision` (load_warden_policy),
    `hivemind.supervision.capping` (deterministic_checks, judge_checks, load_tiers, the judge
    rubrics), `hivemind.wardens` (WardenDeps, ModelJudgeReviewer),
    `hivemind.wardens.snapshot_relay` (RelaySnapshotter),
    `hivemind.wardens.trail_sync` (TrailSyncDeps, WaggleTrailSync), `hivemind.workers.roles`
    (worker_for), `hivemind.cli.in_cell.providers`, `hivemind.cli.in_cell.fanner`,
    `hivemind.cli.in_cell.hive_stand`, `hivemind.cli.in_cell.basket` (the resident Basket) and
    `hivemind.cli.compose.exoskeleton` (in_cell_exoskeleton) and waggle only.

Key invariants:
    - `build_core_deps` is everything a Warden that dials the Queen from a Cell shares, and
      `build_in_cell_warden_deps` adds only what is a Virtual Cell's alone (the Queen-relayed
      snapshotter and the resident Basket) and its own Exoskeleton wiring (`in_cell_exoskeleton`,
      which a colonized device's `device_exoskeleton` differs from in Chromium's sandbox and in
      how long a recording is kept);
      `hivemind.cli.in_cell.borrowed` builds a colonized device's Warden from the same core, so
      the two cannot drift apart.
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
from dataclasses import dataclass
from urllib.parse import urlsplit

from hivemind.cell import CellIdentity, RealCellSource
from hivemind.cli.compose.exoskeleton import in_cell_exoskeleton
from hivemind.cli.in_cell.basket import build_basket_parts
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
from hivemind.llm.routing import RoutingRules
from hivemind.memory import InMemoryMemoryStore, MemoryIdentity
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision import load_warden_policy
from hivemind.supervision.capping import deterministic_checks, judge_checks, load_tiers
from hivemind.supervision.capping.checks.rubrics import load_judge_rubrics
from hivemind.wardens import ModelJudgeReviewer
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.local_pool.hosting import provider_door
from hivemind.wardens.snapshot_relay import RelaySnapshotter
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
    "build_core_deps",
    "build_in_cell_warden_deps",
]


def build_in_cell_warden_deps(
    config: InCellRuntimeConfig,
    source: RealCellSource,
    queen_link: Transport,
    trail: PheromoneTrail,
    clock: Clock,
) -> WardenDeps:
    """Build this Cell's own WardenDeps: manifest-free, from InCellRuntimeConfig and its own deps.

    Args:
        config: This process's own validated runtime config (`config.build_runtime_config`).
        source: This Cell's own one-Cell source, an `InCellSpawnSource` built from
            `config.spawn_config`.
        queen_link: The signed WebSocket transport this Cell already announced CellReady over.
        trail: This Cell's own local Pheromone Trail segment.
        clock: Injected time source shared by every collaborator this composes.

    Returns:
        A WardenDeps ready for `hivemind.wardens.Warden(config.warden_id, deps)`.
    """
    deps, registry = build_core_deps(config, source, queen_link, trail, clock)
    # What only a Virtual Cell's Warden has (a device's, `hivemind.cli.in_cell.borrowed`, borrows
    # a machine that cannot be snapshotted and holds no Basket of the Hive's): the Hive's
    # snapshotter and its resident Basket. The Exoskeleton wiring of roadmap steps 6.4-6.6
    # (module docs) is this Cell's own: a device's differs in Chromium's sandbox and in how long
    # it keeps a recording.
    deps = _with_cell_extras(deps, config, queen_link, clock)
    return in_cell_exoskeleton(registry, clock).apply(deps)


def build_core_deps(
    config: InCellRuntimeConfig,
    source: RealCellSource,
    queen_link: Transport,
    trail: PheromoneTrail,
    clock: Clock,
) -> tuple[WardenDeps, ProviderRegistry]:
    """Build the WardenDeps every Warden that dials the Queen from a Cell shares.

    A Virtual Cell's Warden and a colonized device's (roadmap step 11.6a) differ in a few things
    their composers set; this is the rest. It leaves the memory in process and the lease
    capability `cell:virtual`, for a composer whose Cell is not a Virtual one to replace.

    Args:
        config: This process's own validated runtime config.
        source: Where the Warden leases its one Cell from.
        queen_link: The Warden's link to the Queen (the signed transport, or a reconnecting one).
        trail: The Warden's own local Pheromone Trail segment.
        clock: Injected time source shared by every collaborator this composes.

    Returns:
        The WardenDeps, and the provider registry its model door was built from.
    """
    parts = _Parts(
        registry=build_in_cell_provider_registry(clock, config),
        fanner=build_in_cell_fanner(config, trail, clock),  # One per Cell, whichever registry.
        enforcer=_build_enforcer(config, trail, clock),  # Its policy is also every set's (`guard`).
        source=source,
        queen_link=queen_link,
        trail=trail,
        clock=clock,
    )
    deps = _warden_deps(config, parts)
    deps = _with_trail_sync(deps, config, queen_link, trail, clock)
    routed = _routed(deps, config, parts.fanner, parts.registry)
    return _with_judge(routed, parts.registry, parts.fanner), parts.registry


@dataclass(frozen=True, slots=True)
class _Parts:
    """The collaborators a Cell's deps are built around, made once and shared (codingrules 5.1)."""

    registry: ProviderRegistry  # The Cell's model door.
    fanner: Fanner  # The seat meter every model call passes through.
    enforcer: Enforcer  # The Guard's adapter, whose policy is also every capability set's.
    source: RealCellSource  # Where the Warden leases its Cell from.
    queen_link: Transport  # The Warden's link to the Queen.
    trail: PheromoneTrail  # The Warden's own trail segment.
    clock: Clock


def _warden_deps(config: InCellRuntimeConfig, parts: _Parts) -> WardenDeps:
    """Build the bare WardenDeps of a Cell's Warden: its wiring, policy, Guard and model binding."""
    registry, fanner, enforcer = parts.registry, parts.fanner, parts.enforcer
    return WardenDeps(
        source=parts.source,
        queen_link=parts.queen_link,
        hop=_hop(config),
        memory=InMemoryMemoryStore(parts.trail),
        trail=parts.trail,
        identity=_identity(config),
        clock=parts.clock,
        policy=load_warden_policy(None),  # No [supervision] section inside a Cell: the shipped one.
        tiers=load_tiers(None),  # Same reasoning: the shipped capping-tiers.toml.
        guard=enforcer.policy,
        enforcer=enforcer,  # Roadmap step 10.3.
        lease_capability=VIRTUAL_CELL_LEASE,
        bindings=config.slots,  # What a named binding resolves against at slot_binding.
        local_providers=local_provider_names(config),  # Roadmap step 10.3a.
        checks=deterministic_checks(),  # The JUDGE rung joins later, when a judge is bound.
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


def _routed(
    deps: WardenDeps, config: InCellRuntimeConfig, fanner: Fanner, registry: ProviderRegistry
) -> WardenDeps:
    """Give `deps` this Cell's routing inputs (8.1) and its provider door (8.3).

    The floors are the config's (`llm_floors`): the shipped defaults for a Virtual Cell, whose
    bootstrap ships no `[llm.floors]`, and the device manifest's for a colonized device's Warden,
    which is composed from this same core; the offline flag is the Hive's own. The map is the
    Fanner's, so routing and metering read the same live figures. A Cell carries no
    `[llm.servers]` here (`InCellRuntimeConfig` has no such field), so `servers` stays empty and
    this Warden hosts nothing; the door is wired all the same, so a later config that ships a
    server table needs no change here.
    """
    register, unregister = provider_door(registry, config.providers)
    return dataclasses.replace(
        deps,
        map=fanner.deps.map,
        routing_rules=RoutingRules(floors=config.llm_floors, offline=config.llm_offline),
        servers={},
        register_provider=register,
        unregister_provider=unregister,
    )


def _with_trail_sync(
    deps: WardenDeps,
    config: InCellRuntimeConfig,
    queen_link: Transport,
    trail: PheromoneTrail,
    clock: Clock,
) -> WardenDeps:
    """Give this Warden the way it ships its trail to the Queen, and the keys its Basket uses.

    Split out of `build_core_deps` for its line budget (codingrules 5.1).
    """
    return dataclasses.replace(
        deps,
        trail_sync=_build_trail_sync(config, queen_link, trail, clock),
        # The frame keys double as the Basket's: the manifests this Warden signs and the ones it
        # verifies (the Queen's, who signs with the Hive key) use the keys the link already has.
        node_signer=config.signer,
        node_signer_id=str(config.node_id),
        hive_verifier=config.verifier,
    )


def _with_cell_extras(
    deps: WardenDeps, config: InCellRuntimeConfig, queen_link: Transport, clock: Clock
) -> WardenDeps:
    """Give a Virtual Cell's Warden what it can only do through the Queen, and its own Basket.

    The snapshot is asked of the Queen (this Cell cannot reach its host's Docker daemon or QEMU
    process, ADR-0027), and the resident Basket (roadmap phase 9, ADR-0048) is a small store below
    the Cell's scratch root, which needs no Queen at all but belongs to the same step.
    """
    parts = build_basket_parts(config, clock)
    return dataclasses.replace(
        deps,
        snapshotter=_build_snapshotter(config, queen_link, _hop(config), clock),
        resident_basket=parts[0] if parts is not None else None,
        basket_store=parts[1] if parts is not None else None,
    )


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
            # The frame key doubles as the segment's signer (Waggle 1.13): the Queen requires a
            # Swarm node's segment to carry the node's own signature before she merges it.
            signer=config.signer,
        )
    )
