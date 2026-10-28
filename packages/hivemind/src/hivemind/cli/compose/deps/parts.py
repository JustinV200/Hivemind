"""Build what both deps bags share: HiveStores, HiveParts, the Hive Stand source, Fanner and ledger.

`HiveStores` groups the stores every Hive shares one SQLite file for (mirrors
`hivemind.cli.stores`'s own `open_*` functions, composed together: the flight recorder's store
(roadmap step 6.6, opened with its retention sweep applied), the Honey Store (roadmap phase 7),
the Queen's goal-request table and chat log (roadmap step 10.5), which the Hive Entrance reads
directly, and the Comb Registry and Basket store (roadmap phase 9)). `HiveParts` groups what
`build_warden_deps` and `build_queen_deps` both need (codingrules section 5.1: "introduce a frozen
dataclass for the argument group"), built by `build_hive` only once its own `Fanner` and
`ProviderRegistry` already exist -- `build_hive_stand_source` and `build_fanner` take
`manifest`/`trail`/`clock` directly instead, since `build_hive` calls each of them earlier, before
a `HiveParts` naming their own return values could exist. The four manifest readers at the end
(`system_identity`, `manifest_dir`, `footprints`, `supervision_file`) are the conversions both
bags' builders share.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.deps`. Called by
    `hivemind.cli.compose.hive.build_hive`, the readback commands and this package's `warden` and
    `queen` modules. Calls into `hivemind.brood_chamber`, `hivemind.cell.leavings` (roadmap step
    5.0a), `hivemind.cell.local`, `hivemind.cli.stores`, `hivemind.cli.compose.exoskeleton`
    (the recording store), `hivemind.exoskeleton.recorder`, `hivemind.forage`, `hivemind.guard`,
    `hivemind.honey_store`, `hivemind.llm`, `hivemind.manifest`, `hivemind.memory`,
    `hivemind.pheromone`, `hivemind.queen` (`ForageLedger`, roadmap step 4.7),
    `hivemind.royal_jelly`, `hivemind.basket` and waggle only.

Key invariants:
    - Every identity this module builds stamps `actor="system"`: the composition root itself is
      not a bee (see the package docstring).
    - A hand-built `HiveStores` (a test's in-memory stores) leaves `honey`, `comb_registry` and
      `basket` None, and a Hive built over it runs exactly as before phases 7 and 9.

See Also:
    - hivemind.cli.stores for the `open_*` conversions this module composes rather than repeats.
    - hivemind.cli.compose.deps.warden and .queen for the two bags built over `HiveParts`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from hivemind.basket import BasketStore
from hivemind.brood_chamber import BroodChamber, ChamberIdentity
from hivemind.cell import CellIdentity
from hivemind.cell.leavings import LeavingsStore
from hivemind.cell.local import HiveStandConfig, HiveStandSource
from hivemind.cli.compose.exoskeleton import open_hive_recordings
from hivemind.cli.stores import (
    open_basket_store,
    open_chamber,
    open_chat_log,
    open_comb_registry,
    open_goal_requests,
    open_guard_requests,
    open_honey_store,
    open_leavings,
    open_ledger,
    open_memory,
    open_trail,
)
from hivemind.exoskeleton.recorder import InMemoryRecordingStore, RecordingStore
from hivemind.forage import ForageMap, RoleFootprint, RoyalReserve
from hivemind.guard import Enforcer
from hivemind.honey_store import SqliteHoneyStore
from hivemind.llm import (
    CompositeLlmEventRecorder,
    Fanner,
    FannerDeps,
    ProviderRegistry,
    RateLimit,
    TrailLlmEventRecorder,
)
from hivemind.manifest import HiveManifest
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.queen import ForageLedger
from hivemind.queen.chat import ChatLog
from hivemind.queen.forage.ledger.recorder import LedgerRecorder
from hivemind.queen.guard_requests import GuardRequestStore, InMemoryGuardRequestStore
from hivemind.queen.intake import GoalRequestStore
from hivemind.royal_jelly import CombRegistry, RoyalJellyAccess
from waggle.clock import Clock, SystemClock
from waggle.messages.task import WorkerRole

__all__ = [
    "HiveParts",
    "HiveStores",
    "build_fanner",
    "build_hive_stand_source",
    "build_ledger",
    "footprints",
    "manifest_dir",
    "open_default_stores",
    "supervision_file",
    "system_identity",
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
        goal_requests: The Queen's durable goal-request table (roadmap step 10.5, ADR-0040).
        chat: The Queen's chat log, the human end of her inbox (roadmap step 10.5); the Hive
            Entrance reads both directly, since reading never changes state.
        recordings: The flight recorder's store (roadmap step 6.6), where the Hive Stand's
            Warden records every GUI action. Defaulted to an in-memory store so a `HiveStores` a
            test builds by hand keeps working; `open_default_stores` opens the durable one.
        honey: The Honey Store (roadmap phase 7), opened on the same file by
            `open_default_stores`; None for a hand-built HiveStores (a test's in-memory stores),
            which then builds and runs a Hive exactly as before phase 7.
        comb_registry: The Comb Registry (roadmap phase 9), opened on the same file by
            `open_default_stores`; None for a hand-built HiveStores, which then has no Royal Jelly
            Lab or Basket (`build_royal_jelly` returns None).
        basket: The Basket store (roadmap phase 9), opened on the same file with its byte tree
            under `[basket] root`; None exactly as `comb_registry` is.
        guard_requests: The Queen's durable Guard requests (roadmap step 10.6a, ADR-0043), here
            so a Night Veil teardown clears them too; in memory for a hand-built HiveStores, as
            `recordings` is.
    """

    trail: PheromoneTrail
    chamber: BroodChamber
    memory: MemoryStore
    leavings: LeavingsStore
    goal_requests: GoalRequestStore
    chat: ChatLog
    recordings: RecordingStore = field(default_factory=InMemoryRecordingStore)
    honey: SqliteHoneyStore | None = None
    comb_registry: CombRegistry | None = None
    basket: BasketStore | None = None
    guard_requests: GuardRequestStore = field(default_factory=InMemoryGuardRequestStore)


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
        royal_jelly: The Royal Jelly Lab's and the Basket's handles (roadmap phase 9), built once
            by `build_royal_jelly` and handed to the Queen and the Hive Stand's Warden; None for
            a Hive whose stores carry no Comb Registry or Basket.
    """

    manifest: HiveManifest
    registry: ProviderRegistry
    fanner: Fanner
    stores: HiveStores
    clock: Clock
    enforcer: Enforcer
    royal_jelly: RoyalJellyAccess | None = None


def open_default_stores(manifest: HiveManifest) -> HiveStores:
    """Open every store the Hive keeps in its own `[hive] db` file, recordings and Honey included.

    The recording store is opened with `[exoskeleton] recording_retention_days` applied
    (`hivemind.cli.compose.exoskeleton.open_hive_recordings`): the Hive's one retention sweep for
    recordings runs here, as the Hive starts, on the wall clock.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        A HiveStores over five separate `sqlite3.Connection`s to the same file (ADR-0006: "two
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
        recordings=open_hive_recordings(db, manifest.exoskeleton, SystemClock()),
        honey=open_honey_store(db),  # ADR-0035: the Honey Store lives in the Hive's one file.
        goal_requests=open_goal_requests(db),
        chat=open_chat_log(db),
        comb_registry=open_comb_registry(db),  # ADR-0046: the Comb Registry, in the one file.
        basket=open_basket_store(db, manifest.basket_root(), manifest.basket.max_blob_bytes),
        guard_requests=open_guard_requests(db),
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
    config = HiveStandConfig.from_section(manifest.hive_stand, manifest_dir(manifest))
    # Roadmap step 8.3: the Hive Stand reports `can_host_model` exactly when the operator declared
    # a model server in `[llm.servers]`; no probe can tell whether such a program is installed.
    config = config.model_copy(update={"can_host_model": bool(manifest.llm.servers)})
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    return HiveStandSource(config, identity, trail, clock, leavings)


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
        forage_map: The same live map `hivemind.cli.stores.build_provider_registry` gave the
            registry.
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


def system_identity(manifest: HiveManifest) -> MemoryIdentity:
    """Build the `actor="system"` identity `build_queen_deps` stamps every write with."""
    return MemoryIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")


def manifest_dir(manifest: HiveManifest) -> Path:
    """Return the directory a relative manifest path resolves against.

    Mirrors `HiveManifest.resolve_path`'s own private fallback rule (that method resolves a whole
    *path*; `HiveStandConfig.from_section` needs the bare directory instead).
    """
    return manifest.source_path.parent if manifest.source_path is not None else Path.cwd()


def footprints(roles: Mapping[str, RoleFootprint]) -> dict[WorkerRole, RoleFootprint]:
    """Convert `[forage.roles]`'s lowercase manifest keys into `WorkerRole` members."""
    return {WorkerRole[key.upper()]: footprint for key, footprint in roles.items()}


def supervision_file(manifest: HiveManifest, path: Path | None) -> Path | None:
    """Resolve one `[supervision]` data file, or pass None through for the shipped default.

    An unset `policy_file`/`capping_tiers_file` means "use the table shipped in
    `hivemind.supervision.defaults`", which both loaders express as a None path; only a path the
    operator actually wrote is resolved against the manifest's own directory.
    """
    return manifest.resolve_path(path) if path is not None else None
