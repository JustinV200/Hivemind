"""Convert a loaded Hive Manifest into HiveStores, a ProviderRegistry, a Fanner, and both deps bags.

Codingrules section 13: the composition root is the only place a `HiveManifest` is turned into the
deps every subsystem actually takes; this module is that conversion for roadmap step 3.21's second
half, one function per collaborator so `hivemind.cli.compose.hive.build_hive` stays a short list of
calls. `HiveStores` groups the stores every Hive shares one SQLite file for (mirrors
`hivemind.cli.stores`'s own `open_*` functions, composed together; roadmap step 10.5 adds the
Queen's goal-request table and chat log, which the Hive Entrance reads directly);
`HiveParts` groups what `build_warden_deps` and `build_queen_deps` both need (codingrules section
5.1: "introduce a frozen dataclass for the argument group"), built by `build_hive` only once its
own `Fanner` and `ProviderRegistry` already exist -- `build_hive_stand_source` and `build_fanner`
take `manifest`/`trail`/`clock` directly instead, since `build_hive` calls each of them earlier,
before a `HiveParts` naming their own return values could exist. Roadmap step 10.3: `build_enforcer`
builds the one Guard `Enforcer` (over `[guard]`'s policy) that the Queen and the Hive Stand's Warden
share, carried on `HiveParts.enforcer`; the Warden's lease needs `cell:hive_stand`, because this
module is the one place that knows it built the Hive Stand's own source.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive`. Calls into `hivemind.brood_chamber`,
    `hivemind.cell.leavings` (roadmap step 5.0a), `hivemind.cell.local`, `hivemind.cli.stores`,
    `hivemind.forage`, `hivemind.guard` (the Guard policy, roadmap step 10.2), `hivemind.llm`,
    `hivemind.manifest`, `hivemind.memory`, `hivemind.pheromone`, `hivemind.queen`
    (`ForageLedger`, roadmap step 4.7), `hivemind.supervision`, `hivemind.wardens`,
    `hivemind.workers` and waggle only.

Key invariants:
    - Every identity this module builds (`MemoryIdentity`, `ChamberIdentity`, `CellIdentity`)
      stamps `actor="system"`: the composition root itself is not a bee, and every write it makes
      on a bee's behalf is attributed the same way `tests.builders.queen`/`.wardens` already do.
    - `build_provider_registry`'s `"fake"` factory substitution is selected by `ProviderConfig.
      kind`, never by branching on a provider's own name or kind elsewhere (codingrules section 4;
      `scripts/check_no_kind_branches.py`); see that function's own docstring.
    - `build_warden_deps`'s `worker_factory` always returns a fresh `hivemind.workers.roles.Drone`:
      v0's only Worker role (`hivemind.manifest.schema.forage.REQUIRED_ROLE`).

See Also:
    - .claude/codingrules.md section 13 for "the composition root is the only place a HiveManifest
      is converted".
    - hivemind.cli.stores for open_trail/open_chamber/open_memory/build_registry/build_forage_map/
      slot_bindings, the conversions this module composes rather than repeats.
    - hivemind.queen.deps and hivemind.wardens.deps for QueenDeps and WardenDeps, the two bags
      this module's two builder functions return.
    - hivemind.cli.compose.hive for build_hive, this module's one caller.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from hivemind.brood_chamber import BroodChamber, ChamberIdentity
from hivemind.cell import CellIdentity
from hivemind.cell.leavings import LeavingsStore
from hivemind.cell.local import HiveStandConfig, HiveStandSource
from hivemind.cli.compose.links import HiveLinks
from hivemind.cli.compose.virtual_cells import VirtualCellsParts
from hivemind.cli.stores import (
    build_registry,
    open_chamber,
    open_chat_log,
    open_cluster_orders,
    open_goal_requests,
    open_leavings,
    open_ledger,
    open_memory,
    open_trail,
    slot_bindings,
)
from hivemind.forage import ForageMap, GoalBudgets, ModelSlot, RoleFootprint, RoyalReserve, Tempo
from hivemind.guard import Capability, CapabilityFamily, Enforcer, GuardPolicy, load_guard_policy
from hivemind.llm import (
    CallGate,
    CompositeLlmEventRecorder,
    FakeLLMProvider,
    Fanner,
    FannerDeps,
    LLMProvider,
    ProviderCapabilities,
    ProviderConfig,
    ProviderFactory,
    ProviderRegistry,
    RateLimit,
    Responder,
    TrailLlmEventRecorder,
    apply_overrides,
    default_factories,
)
from hivemind.manifest import ForageSection, HiveManifest
from hivemind.manifest.schema import PlacementSection
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.queen import ForageLedger, MemoryBudget, QueenDeps
from hivemind.queen.chat import ChatLog
from hivemind.queen.forage.ledger.recorder import LedgerRecorder
from hivemind.queen.intake import GoalRequestStore
from hivemind.queen.placement import PlacementPolicy
from hivemind.supervision import load_policy
from hivemind.supervision.capping import deterministic_checks, judge_checks, load_tiers
from hivemind.supervision.capping.checks.rubrics import load_judge_rubrics
from hivemind.wardens import ModelJudgeReviewer, WardenDeps
from hivemind.workers import Worker
from hivemind.workers.roles import Drone
from waggle.clock import Clock
from waggle.messages.task import WorkerRole

# Roadmap step 10.3: what the Hive Stand's own Warden's lease needs of its set (the lease_creation
# point); set here because this module built the Hive Stand's source, never read off a Cell's kind.
HIVE_STAND_LEASE = Capability(family=CapabilityFamily.CELL_HIVE_STAND)

__all__ = [
    "HIVE_STAND_LEASE",
    "HiveParts",
    "HiveStores",
    "build_enforcer",
    "build_fanner",
    "build_hive_stand_source",
    "build_ledger",
    "build_provider_registry",
    "build_queen_deps",
    "build_warden_deps",
    "open_default_stores",
]


@dataclass(frozen=True, slots=True)
class HiveStores:
    """The stores every Hive opens against its own `[hive] db` file.

    Attributes:
        trail: The Pheromone Trail every subsystem records to.
        chamber: The Brood Chamber the Queen submits and advances tasks through.
        memory: Where every Pin, Note, Handoff and episode this Hive writes lives.
        leavings: The Leavings ledger `build_hive_stand_source` hands to every
            `HiveStandLeaseReleaser` this Hive builds (roadmap step 5.0a).
        goal_requests: The Queen's durable goal-request table (roadmap step 10.5, ADR-0032).
        chat: The Queen's chat log, the human end of her inbox (roadmap step 10.5); the Hive
            Entrance reads both directly, since reading never changes state.
    """

    trail: PheromoneTrail
    chamber: BroodChamber
    memory: MemoryStore
    leavings: LeavingsStore
    goal_requests: GoalRequestStore
    chat: ChatLog


@dataclass(frozen=True, slots=True)
class HiveParts:
    """What `build_warden_deps` and `build_queen_deps` both need (codingrules section 5.1).

    Built once by `build_hive`, after its own `Fanner` and `ProviderRegistry` already exist
    (`build_hive_stand_source` and `build_fanner` take `manifest`/`trail`/`clock` directly instead,
    since each is built before this bundle can be, per their own docstrings), instead of threading
    five collaborators through both builder functions' own parameter lists.

    Attributes:
        manifest: The loaded HiveManifest every builder converts a slice of.
        registry: The ProviderRegistry every `BoundModel` resolves through.
        fanner: The Fanner every `CallGate` this Hive hands out is a lane of.
        stores: This Hive's trail, chamber, memory and leavings stores.
        clock: Injected time source shared by every collaborator this composes.
        enforcer: The Guard's one Enforcer (`build_enforcer`, roadmap step 10.3), shared by the
            Queen and the Hive Stand's Warden so both decide against the same policy.
    """

    manifest: HiveManifest
    registry: ProviderRegistry
    fanner: Fanner
    stores: HiveStores
    clock: Clock
    enforcer: Enforcer


def open_default_stores(manifest: HiveManifest) -> HiveStores:
    """Open the Hive's own `[hive] db` file as its trail, chamber, memory and leavings store.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        A HiveStores over four separate `sqlite3.Connection`s to the same file (ADR-0006: "two
        stores that write the same file use separate connections; WAL makes that fine").
    """
    db = manifest.resolve_path(manifest.hive.db)
    identity = ChamberIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
    )
    return HiveStores(
        trail=open_trail(db),
        chamber=open_chamber(db, identity),
        memory=open_memory(db),
        leavings=open_leavings(db),
        goal_requests=open_goal_requests(db),
        chat=open_chat_log(db),
    )


def build_hive_stand_source(
    manifest: HiveManifest, trail: PheromoneTrail, clock: Clock, leavings: LeavingsStore
) -> HiveStandSource:
    """Build the Hive Stand's own RealCellSource from `[hive_stand]`.

    Takes `trail`/`clock`/`leavings` directly rather than a `HiveParts` (unlike `build_warden_
    deps`/`build_queen_deps` below): a `HiveParts` also carries the `Fanner` and `ProviderRegistry`
    this function has no use for, and `build_hive` needs this source's one Cell (`source.cells()`)
    before either of those exists, to seed `hivemind.cli.compose.links.build_hive_links`.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        trail: Where `cell.leased`/`cell.released` land.
        clock: Injected time source for every id minted and every timestamp written.
        leavings: Where a `persist=True` restore record's Leaving row lands on release (roadmap
            step 5.0a); a caller that never leases (`hivemind.cli.readback.cells`'s own `hive
            cells list`) may pass a throwaway `InMemoryLeavingsStore()`.

    Returns:
        A HiveStandSource whose one Cell is not yet leased (`hivemind.wardens.warden.Warden.start`
        leases it once `hivemind.cli.compose.hive.run_hive` starts the Hive).
    """
    config = HiveStandConfig.from_section(manifest.hive_stand, _manifest_dir(manifest))
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    return HiveStandSource(config, identity, trail, clock, leavings)


def build_provider_registry(
    manifest: HiveManifest,
    environ: Mapping[str, str],
    clock: Clock,
    forage_map: ForageMap,
    responders: Mapping[str, Responder] | None,
) -> ProviderRegistry:
    """Build a ProviderRegistry sharing `forage_map`, optionally scripting every `"fake"` provider.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        environ: The composition root's own environment mapping, for provider API keys.
        clock: Passed to every provider this registry later constructs.
        forage_map: Shared with the Fanner and `QueenDeps.map`: one live map for the whole Hive,
            never a second instance the routing figures the Fanner writes never reach.
        responders: When given and non-empty, every `[llm.providers.<name>] kind = "fake"` row is
            built with `responders.get(name)` installed as its `hivemind.llm.fake.FakeLLMProvider.
            __init__`'s own `responder`, so a test can script one without reaching into the
            registry's private cache after the fact. `None` or empty keeps
            `hivemind.llm.registry.default_factories`'s own plain `FakeLLMProvider` unchanged.

    Returns:
        A ProviderRegistry ready to resolve any `[llm.slots]` binding this manifest declares.
    """
    factories = dict(default_factories())
    if responders:
        factories["fake"] = _responder_installing_fake_factory(responders)
    return build_registry(manifest, environ, clock, factories=factories, forage_map=forage_map)


def build_fanner(
    manifest: HiveManifest,
    forage_map: ForageMap,
    trail: PheromoneTrail,
    clock: Clock,
    ledger: ForageLedger | None = None,
) -> Fanner:
    """Build the one Fanner every bee's `CallGate` in this Hive shares.

    Takes `trail`/`clock` directly rather than a `HiveParts` (see `build_hive_stand_source`'s own
    docstring for why): `build_hive` builds this Fanner before `HiveParts` itself exists, since
    `HiveParts.fanner` is this function's own return value.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`; `[llm.providers]`
            sizes every provider's seats and rate limit.
        forage_map: The same live map `build_provider_registry` gave the registry.
        trail: Where every `llm.call`/`llm.spill` occurrence is recorded.
        clock: Injected time source for every wait and every recorded event.
        ledger: The Queen's live book of Forage (roadmap step 4.8's own wiring step), built by
            `build_ledger` before this call in `hivemind.cli.compose.hive.build_hive` (ahead of
            its usual place in `build_queen_deps`, so it exists in time for this call). When
            given, every recorded occurrence also feeds `hivemind.queen.forage.ledger.recorder.
            LedgerRecorder`, so an `llm.call`'s own seat and spend land in the ledger the moment
            it happens, not only on the trail. `None` (every pre-4.8-wiring caller) keeps this
            Fanner recording to the trail alone.

    Returns:
        A Fanner ready to hand out `Fanner.lane(tempo)` `CallGate`s.
    """
    seats = {name: spec.seats for name, spec in manifest.llm.providers.items()}
    limits = {
        name: RateLimit(
            requests_per_minute=spec.requests_per_minute, tokens_per_minute=spec.tokens_per_minute
        )
        for name, spec in manifest.llm.providers.items()
    }
    trail_recorder = TrailLlmEventRecorder(
        trail, manifest.hive.id, manifest.hive.node_id, "system", clock
    )
    recorder = (
        CompositeLlmEventRecorder(LedgerRecorder(ledger), trail_recorder)
        if ledger is not None
        else trail_recorder
    )
    deps = FannerDeps(map=forage_map, seats=seats, limits=limits, clock=clock, recorder=recorder)
    return Fanner(deps)


def build_warden_deps(parts: HiveParts, source: HiveStandSource, links: HiveLinks) -> WardenDeps:
    """Build the Hive Stand's own WardenDeps from `[supervision]`/`[memory]` and the shared parts.

    Args:
        parts: This Hive's shared collaborators.
        source: The Hive Stand's own RealCellSource: leased and released, never provisioned.
        links: Both ends of the Queen<->Warden link (`hivemind.cli.compose.links.build_hive_
            links`); `links.warden_transport`/`.warden_hop` are this Warden's own end.

    Returns:
        A WardenDeps ready for `hivemind.wardens.Warden(links.warden_id, deps)`.
    """
    manifest = parts.manifest
    supervision = manifest.supervision
    # Roadmap step 4.10: a model-backed JudgeReviewer, merged into the deterministic check
    # registry so CheckKind.JUDGE is available wherever a tier's own `judge` flag turns it on.
    judge_rubrics = load_judge_rubrics()
    judge_reviewer = _build_judge_reviewer(parts)
    return WardenDeps(
        source=source,
        queen_link=links.warden_transport,
        hop=links.warden_hop,
        memory=parts.stores.memory,
        trail=parts.stores.trail,
        identity=_system_identity(manifest),
        clock=parts.clock,
        policy=load_policy(_supervision_file(manifest, supervision.policy_file)),
        tiers=load_tiers(_supervision_file(manifest, supervision.capping_tiers_file)),
        checks={**deterministic_checks(), **judge_checks(judge_reviewer, judge_rubrics)},
        bound=parts.registry.bound(ModelSlot.WARDEN),
        call_gate=parts.fanner.lane(Tempo()),
        worker_factory=_build_drone,
        rebind=lambda key: parts.registry.bound_for_key(key, ModelSlot.WORKER),
        handoff_threshold=manifest.memory.handoff_threshold,
        heartbeat_interval_s=supervision.heartbeat_interval_s,
        worker_heartbeat_interval_s=supervision.heartbeat_interval_s,
        missed_heartbeats_before_stalled=supervision.heartbeat_miss_limit,
        judge_reviewer=judge_reviewer,
        judge_rubrics=judge_rubrics,
        # Roadmap step 4.8: one Fanner lane per grant (WardenDeps.lane_for_grant's own docstring).
        lane_for_grant=_lane_for_grant(parts),
        # Roadmap step 5.0e: resolved the same way build_queen_deps resolves scratch_root.
        keep_root=_keep_root(manifest),
        disk_reserve_mb=manifest.hive_stand.disk_reserve_mb,
        guard=parts.enforcer.policy,  # Roadmap step 10.2: every set this Warden builds.
        # Roadmap step 10.3: the Guard's adapter, its lease's need, the slot_binding slot table.
        enforcer=parts.enforcer,
        lease_capability=HIVE_STAND_LEASE,
        bindings=slot_bindings(manifest),
    )


def build_enforcer(manifest: HiveManifest, trail: PheromoneTrail, clock: Clock) -> Enforcer:
    """Build the Hive's one Guard Enforcer over `[guard]`'s policy (roadmap step 10.3).

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`; `[guard]` is read.
        trail: Where every refusal's `guard.denied` row lands.
        clock: Mints each refusal's id and timestamp.

    Returns:
        An Enforcer recording as `actor="system"`, like every identity this module builds.
    """
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    return Enforcer(_guard_policy(manifest), trail, clock, identity)


def _guard_policy(manifest: HiveManifest) -> GuardPolicy:
    """Build the Guard policy from `[guard]`: its policy file (or the shipped one), overlaid.

    `policy_file` resolves against the manifest's own directory like every other manifest path
    (`_supervision_file`); an empty one means the policy shipped in `hivemind.guard.defaults`.
    """
    section = manifest.guard
    path = manifest.resolve_path(Path(section.policy_file)) if section.policy_file else None
    return load_guard_policy(path, section)


def _keep_root(manifest: HiveManifest) -> Path | None:
    """Resolve `[hive_stand] keep_root` against the manifest's own directory, or None."""
    keep_root = manifest.hive_stand.keep_root
    return manifest.resolve_path(keep_root) if keep_root is not None else None


def _build_judge_reviewer(parts: HiveParts) -> ModelJudgeReviewer:
    """Build the Warden-layer JudgeReviewer on `ModelSlot.JUDGE`, one Fanner lane per review."""
    # `Fanner.lane` itself is the lane factory: each review gets a lane on the proposal's own
    # tempo, so an urgent task's judge call queues ahead of a thorough one's.
    return ModelJudgeReviewer(
        bound=parts.registry.bound(ModelSlot.JUDGE), lane_for=parts.fanner.lane
    )


def _lane_for_grant(parts: HiveParts) -> Callable[[str, str, Tempo], CallGate]:
    """Return `WardenDeps.lane_for_grant`: one Fanner lane per (grant, goal) on the task's tempo."""
    return lambda grant_id, goal_id, tempo: parts.fanner.lane(
        tempo, grant_id=grant_id, goal_id=goal_id
    )


def build_queen_deps(
    parts: HiveParts,
    forage_map: ForageMap,
    ledger: ForageLedger,
    virtual_cells: VirtualCellsParts | None = None,
) -> QueenDeps:
    """Build the Queen's own QueenDeps from `[forage]`/`[supervision]`/`[memory]` and shared parts.

    Args:
        parts: This Hive's shared collaborators.
        forage_map: The same live map `build_provider_registry`/`build_fanner` share.
        ledger: The Queen's live book of Forage, built by `build_ledger` ahead of `build_fanner`
            (roadmap step 4.8's own wiring step: `build_fanner` needs it too, before `QueenDeps`
            itself can exist to carry it).
        virtual_cells: `hivemind.cli.compose.virtual_cells.build_virtual_cells`'s own return
            value, when `[virtual_cells] backend` is set; folded into `QueenDeps.virtual_provider`/
            `.virtual_backend_source`/`.dormant_cell_source`/`.on_task_finished`. `None` (the
            default, and every pre-5.6 caller) leaves those four fields at their own defaults,
            keeping placement Real-only.

    Returns:
        A QueenDeps ready for `hivemind.queen.Queen(deps)`.
    """
    base = _base_queen_deps(parts, forage_map, ledger)
    return _with_virtual_cells(base, virtual_cells)


def _base_queen_deps(parts: HiveParts, forage_map: ForageMap, ledger: ForageLedger) -> QueenDeps:
    """Build every `QueenDeps` field `build_queen_deps` set before roadmap step 5.6's own field."""
    manifest = parts.manifest
    supervision = manifest.supervision
    forage = manifest.forage
    return QueenDeps(
        chamber=parts.stores.chamber,
        memory=parts.stores.memory,
        trail=parts.stores.trail,
        identity=_system_identity(manifest),
        clock=parts.clock,
        policy=load_policy(_supervision_file(manifest, supervision.policy_file)),
        enforcer=parts.enforcer,  # Roadmap step 10.3: every enforcement point the Queen passes.
        goal_requests=parts.stores.goal_requests,  # Roadmap step 10.5: her durable goal requests.
        chat=parts.stores.chat,  # Roadmap step 10.5: the human end of her inbox.
        bound_for=parts.registry.bound,
        rebind=parts.registry.bound_for_key,
        bindings=slot_bindings(manifest),
        call_gate=parts.fanner.lane(Tempo()),
        map=forage_map,
        budgets=_goal_budgets(forage),
        heartbeat_interval_s=supervision.heartbeat_interval_s,
        heartbeat_miss_limit=supervision.heartbeat_miss_limit,
        alarm_attempt_limit=supervision.alarm_attempt_limit,
        memory_budget=_awake_memory_budget(manifest),
        scratch_root=manifest.resolve_path(manifest.hive_stand.scratch_root),  # roadmap 5.0b
        footprints=_footprints(forage.roles),
        reserve=forage.reserve,
        grant_ttl_s=forage.grant_ttl_s,
        # roadmap step 4.8: the same ForageLedger build_hive already built (see build_queen_deps's
        # own docstring), restored from whatever its SqliteLedgerStore already held.
        ledger=ledger,
        # Roadmap step 4.9: a SqliteOrderStore over the same [hive] db file (hive cluster/wake
        # share it); provider_lookup satisfies HealthPoller.probe's one collaborator.
        orders=open_cluster_orders(manifest.resolve_path(manifest.hive.db)),
        provider_lookup=parts.registry.provider,
        # Roadmap step 4.3: the manifest's own sweep cadence for the Queen's House Bee sweep.
        sweep_interval_s=manifest.memory.sweep_interval_s,
        hot_window_s=manifest.memory.hot_window_s,
        # Roadmap step 5.7: the [placement] section, as the slice decide() reads.
        placement_policy=_placement_policy(manifest.placement),
    )


def _placement_policy(section: PlacementSection) -> PlacementPolicy:
    """Convert the manifest's `[placement]` section into the `PlacementPolicy` decide() reads."""
    return PlacementPolicy(
        prefer=section.prefer,
        allow_hive_stand=section.allow_hive_stand,
        role_overrides={
            key: override.prefer
            for key, override in section.roles.items()
            if override.prefer is not None
        },
    )


def _with_virtual_cells(base: QueenDeps, virtual_cells: VirtualCellsParts | None) -> QueenDeps:
    """Fold the live Virtual Cell seam into `base`, additive; `base` unchanged when `None`."""
    if virtual_cells is None:
        return base
    return dataclasses.replace(
        base,
        virtual_provider=virtual_cells.provider,
        virtual_backend_source=virtual_cells.virtual_backend_source,
        dormant_cell_source=virtual_cells.dormant_cell_source,
        on_task_finished=virtual_cells.on_task_finished,
        on_cell_granted=virtual_cells.on_cell_granted,
    )


def _system_identity(manifest: HiveManifest) -> MemoryIdentity:
    """Build the `actor="system"` identity `build_queen_deps` stamps every write with."""
    return MemoryIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")


def _goal_budgets(forage: ForageSection) -> GoalBudgets:
    """Build the `[forage]` goal-level caps `build_queen_deps`'s own `budgets` field carries."""
    return GoalBudgets(
        spend_cap_usd=forage.spend_cap_per_goal_usd,
        token_budget=forage.token_budget_per_goal,
        max_sub_bees=forage.max_sub_bees_per_goal,
    )


def _awake_memory_budget(manifest: HiveManifest) -> MemoryBudget:
    """Build the `[memory]` slice `build_queen_deps`'s own `memory_budget` field carries."""
    return MemoryBudget(
        budget_fraction=manifest.memory.budget_fraction,
        output_reserve_tokens=manifest.memory.output_reserve_tokens,
    )


def build_ledger(manifest: HiveManifest, reserve: RoyalReserve) -> ForageLedger:
    """Build a ForageLedger over a SqliteLedgerStore on `manifest`'s own `[hive] db` file.

    Roadmap step 4.8: opening the store and restoring the ledger from it both need
    `asyncio.run`'s own fresh event loop, the same composition-root seam
    `hivemind.cli.stores.open_trail` already uses (codingrules section 8.2) -- `open_ledger`
    itself already does this for the open; `restore` needs its own call since it is a method on
    an already-built `ForageLedger`, not a classmethod `open_ledger` could return in one step.
    Public (not `_build_ledger`, this function's own pre-4.8-wiring name): `hivemind.cli.compose.
    hive.build_hive` now calls this ahead of both `build_fanner` and `build_queen_deps`, since the
    Fanner's own recorder needs the ledger too (roadmap step 4.8's own wiring step).

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`; `[hive] db` names
            the file every store this Hive opens shares.
        reserve: The `[forage.reserve]` Royal Reserve this ledger's headroom subtracts first.

    Returns:
        A ForageLedger whose in-memory tables already reflect whatever this Hive's database file
        held before this call (empty, for a fresh one).
    """
    db = manifest.resolve_path(manifest.hive.db)
    store = open_ledger(db)
    ledger = ForageLedger(reserve=reserve, store=store)
    asyncio.run(ledger.restore())
    return ledger


def _manifest_dir(manifest: HiveManifest) -> Path:
    """Return the directory a relative manifest path resolves against.

    Mirrors `HiveManifest.resolve_path`'s own private fallback rule (that method resolves a whole
    *path*; `HiveStandConfig.from_section` needs the bare directory instead).
    """
    return manifest.source_path.parent if manifest.source_path is not None else Path.cwd()


def _footprints(roles: Mapping[str, RoleFootprint]) -> dict[WorkerRole, RoleFootprint]:
    """Convert `[forage.roles]`'s lowercase manifest keys into `WorkerRole` members."""
    return {WorkerRole[key.upper()]: footprint for key, footprint in roles.items()}


def _build_drone(role: WorkerRole) -> Worker:
    """Return a fresh Drone for every `TaskAssign.role`; v0's only Worker role (see module docs)."""
    return Drone()


def _responder_installing_fake_factory(responders: Mapping[str, Responder]) -> ProviderFactory:
    """Build a `"fake"` ProviderFactory that installs a scripted Responder, if one is named.

    Not a `cell.kind`/`provider.name` branch (`scripts/check_no_kind_branches.py`): this factory
    is reached only because `ProviderConfig.kind == "fake"` already selected it, exactly the way
    `hivemind.llm.registry.default_factories` itself dispatches by kind for every adapter;
    `responders.get(name)` only varies what that one already-selected factory builds, the same
    way `hivemind.llm.registry.apply_overrides` already varies a provider's declared capabilities.
    """

    def factory(
        name: str, config: ProviderConfig, api_key: SecretStr | None, clock: Clock
    ) -> LLMProvider:
        """Build a FakeLLMProvider for `name`, scripted with `responders[name]` when present."""
        capabilities = apply_overrides(ProviderCapabilities.full(), config.capability_overrides)
        return FakeLLMProvider(
            name=name, capabilities=capabilities, responder=responders.get(name), clock=clock
        )

    return factory


def _supervision_file(manifest: HiveManifest, path: Path | None) -> Path | None:
    """Resolve one `[supervision]` data file, or pass None through for the shipped default.

    An unset `policy_file`/`capping_tiers_file` means "use the table shipped in
    `hivemind.supervision.defaults`", which both loaders express as a None path; only a path the
    operator actually wrote is resolved against the manifest's own directory.
    """
    return manifest.resolve_path(path) if path is not None else None
