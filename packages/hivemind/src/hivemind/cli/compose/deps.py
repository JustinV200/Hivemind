"""Convert a loaded Hive Manifest into HiveStores, a ProviderRegistry, a Fanner, and both deps bags.

Codingrules section 13: the composition root is the only place a `HiveManifest` is turned into the
deps every subsystem actually takes; this module is that conversion for roadmap step 3.21's second
half, one function per collaborator so `hivemind.cli.compose.hive.build_hive` stays a short list of
calls. `HiveStores` groups the three stores every Hive shares one SQLite file for (mirrors
`hivemind.cli.stores`'s own `open_trail`/`open_chamber`/`open_memory`, now composed together);
`HiveParts` groups what `build_warden_deps` and `build_queen_deps` both need (codingrules section
5.1: "introduce a frozen dataclass for the argument group"), built by `build_hive` only once its
own `Fanner` and `ProviderRegistry` already exist -- `build_hive_stand_source` and `build_fanner`
take `manifest`/`trail`/`clock` directly instead, since `build_hive` calls each of them earlier,
before a `HiveParts` naming their own return values could exist.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose`. Called by
    `hivemind.cli.compose.hive.build_hive`. Calls into `hivemind.brood_chamber`,
    `hivemind.cell.local`, `hivemind.cli.stores`, `hivemind.forage`, `hivemind.llm`,
    `hivemind.manifest`, `hivemind.memory`, `hivemind.pheromone`, `hivemind.queen`,
    `hivemind.supervision`, `hivemind.wardens`, `hivemind.workers` and waggle only.

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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from hivemind.brood_chamber import BroodChamber, ChamberIdentity
from hivemind.cell import CellIdentity
from hivemind.cell.local import HiveStandConfig, HiveStandSource
from hivemind.cli.compose.links import HiveLinks
from hivemind.cli.stores import build_registry, open_chamber, open_memory, open_trail, slot_bindings
from hivemind.forage import ForageMap, GoalBudgets, ModelSlot, RoleFootprint, Tempo
from hivemind.llm import (
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
from hivemind.manifest import HiveManifest
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.queen import MemoryBudget, QueenDeps
from hivemind.supervision import load_policy
from hivemind.supervision.capping import deterministic_checks, load_tiers
from hivemind.wardens import WardenDeps
from hivemind.workers import Worker
from hivemind.workers.roles import Drone
from waggle.clock import Clock
from waggle.messages.task import WorkerRole

__all__ = [
    "HiveParts",
    "HiveStores",
    "build_fanner",
    "build_hive_stand_source",
    "build_provider_registry",
    "build_queen_deps",
    "build_warden_deps",
    "open_default_stores",
]


@dataclass(frozen=True, slots=True)
class HiveStores:
    """The three stores every Hive opens against its own `[hive] db` file.

    Attributes:
        trail: The Pheromone Trail every subsystem records to.
        chamber: The Brood Chamber the Queen submits and advances tasks through.
        memory: Where every Pin, Note, Handoff and episode this Hive writes lives.
    """

    trail: PheromoneTrail
    chamber: BroodChamber
    memory: MemoryStore


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
        stores: This Hive's trail, chamber and memory store.
        clock: Injected time source shared by every collaborator this composes.
    """

    manifest: HiveManifest
    registry: ProviderRegistry
    fanner: Fanner
    stores: HiveStores
    clock: Clock


def open_default_stores(manifest: HiveManifest) -> HiveStores:
    """Open the Hive's own `[hive] db` file as its trail, chamber and memory store.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        A HiveStores over three separate `sqlite3.Connection`s to the same file (ADR-0006: "two
        stores that write the same file use separate connections; WAL makes that fine").
    """
    db = manifest.resolve_path(manifest.hive.db)
    identity = ChamberIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
    )
    return HiveStores(
        trail=open_trail(db), chamber=open_chamber(db, identity), memory=open_memory(db)
    )


def build_hive_stand_source(
    manifest: HiveManifest, trail: PheromoneTrail, clock: Clock
) -> HiveStandSource:
    """Build the Hive Stand's own RealCellSource from `[hive_stand]`.

    Takes `trail`/`clock` directly rather than a `HiveParts` (unlike `build_warden_deps`/
    `build_queen_deps` below): a `HiveParts` also carries the `Fanner` and `ProviderRegistry` this
    function has no use for, and `build_hive` needs this source's one Cell (`source.cells()`)
    before either of those exists, to seed `hivemind.cli.compose.links.build_hive_links`.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        trail: Where `cell.leased`/`cell.released` land.
        clock: Injected time source for every id minted and every timestamp written.

    Returns:
        A HiveStandSource whose one Cell is not yet leased (`hivemind.wardens.warden.Warden.start`
        leases it once `hivemind.cli.compose.hive.run_hive` starts the Hive).
    """
    config = HiveStandConfig.from_section(manifest.hive_stand, _manifest_dir(manifest))
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    return HiveStandSource(config, identity, trail, clock)


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
    manifest: HiveManifest, forage_map: ForageMap, trail: PheromoneTrail, clock: Clock
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
    recorder = TrailLlmEventRecorder(
        trail, manifest.hive.id, manifest.hive.node_id, "system", clock
    )
    deps = FannerDeps(map=forage_map, seats=seats, limits=limits, clock=clock, recorder=recorder)
    return Fanner(deps)


def build_warden_deps(parts: HiveParts, source: HiveStandSource, links: HiveLinks) -> WardenDeps:
    """Build the Hive Stand's own WardenDeps from `[supervision]`/`[memory]` and the shared parts.

    Args:
        parts: This Hive's shared collaborators.
        source: The Hive Stand's own RealCellSource (`build_hive_stand_source`); the Warden leases
            and releases through it, never provisions (CLAUDE.md).
        links: Both ends of the Queen<->Warden link (`hivemind.cli.compose.links.build_hive_
            links`); `links.warden_transport`/`.warden_hop` are this Warden's own end.

    Returns:
        A WardenDeps ready for `hivemind.wardens.Warden(links.warden_id, deps)`.
    """
    manifest = parts.manifest
    supervision = manifest.supervision
    identity = MemoryIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
    )
    return WardenDeps(
        source=source,
        queen_link=links.warden_transport,
        hop=links.warden_hop,
        memory=parts.stores.memory,
        trail=parts.stores.trail,
        identity=identity,
        clock=parts.clock,
        policy=load_policy(_supervision_file(manifest, supervision.policy_file)),
        tiers=load_tiers(_supervision_file(manifest, supervision.capping_tiers_file)),
        checks=deterministic_checks(),
        bound=parts.registry.bound(ModelSlot.WARDEN),
        call_gate=parts.fanner.lane(Tempo()),
        worker_factory=_build_drone,
        rebind=lambda key: parts.registry.bound_for_key(key, ModelSlot.WORKER),
        handoff_threshold=manifest.memory.handoff_threshold,
        heartbeat_interval_s=supervision.heartbeat_interval_s,
        worker_heartbeat_interval_s=supervision.heartbeat_interval_s,
        missed_heartbeats_before_stalled=supervision.heartbeat_miss_limit,
    )


def build_queen_deps(parts: HiveParts, forage_map: ForageMap) -> QueenDeps:
    """Build the Queen's own QueenDeps from `[forage]`/`[supervision]`/`[memory]` and shared parts.

    Args:
        parts: This Hive's shared collaborators.
        forage_map: The same live map `build_provider_registry`/`build_fanner` share.

    Returns:
        A QueenDeps ready for `hivemind.queen.Queen(deps)`.
    """
    manifest = parts.manifest
    supervision = manifest.supervision
    forage = manifest.forage
    identity = MemoryIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
    )
    return QueenDeps(
        chamber=parts.stores.chamber,
        memory=parts.stores.memory,
        trail=parts.stores.trail,
        identity=identity,
        clock=parts.clock,
        policy=load_policy(_supervision_file(manifest, supervision.policy_file)),
        bound_for=parts.registry.bound,
        rebind=parts.registry.bound_for_key,
        bindings=slot_bindings(manifest),
        call_gate=parts.fanner.lane(Tempo()),
        map=forage_map,
        budgets=GoalBudgets(
            spend_cap_usd=forage.spend_cap_per_goal_usd,
            token_budget=forage.token_budget_per_goal,
            max_sub_bees=forage.max_sub_bees_per_goal,
        ),
        heartbeat_interval_s=supervision.heartbeat_interval_s,
        heartbeat_miss_limit=supervision.heartbeat_miss_limit,
        alarm_attempt_limit=supervision.alarm_attempt_limit,
        memory_budget=MemoryBudget(
            budget_fraction=manifest.memory.budget_fraction,
            output_reserve_tokens=manifest.memory.output_reserve_tokens,
        ),
        footprints=_footprints(forage.roles),
        reserve=forage.reserve,
        grant_ttl_s=forage.grant_ttl_s,
    )


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
