"""Define Hive and build_hive: turn a loaded Hive Manifest into a Hive's every collaborator.

Roadmap step 3.21 (second half)'s own composition root: `build_hive` is the *only* place a loaded
`hivemind.manifest.HiveManifest` becomes a running Hive's every collaborator (codingrules section
13), built by delegating each conversion to `hivemind.cli.compose.deps` and the one Queen<->Warden
link to `hivemind.cli.compose.links`; nothing it builds is started (`hivemind.cli.compose.hive.run`
starts it). When the stores include a Honey Store (roadmap phase 7, the Hive's searchable
knowledge base), `build_hive` builds its handles once (`hivemind.cli.compose.honey.
build_honey_access`) for the Queen's `QueenDeps.honey`, and the House Bee's ripening loop
(`hivemind.workers.roles.house_bee.HouseBeeRipening`) over them. The zero-grant fix wires two
things here: the Hive Stand's Queen-side link reads the Stand's capacity as it stands on every
grant (`hivemind.queen.deps.WardenLink.live_capacity`), and the Queen gets `[forage]
zero_grant_patience_s`, how long a fresh task may wait for that capacity to make room.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.compose.hive`. Calls into
    `hivemind.cell`, `hivemind.cli.compose.deps`, `.guard`, `.honey`, `.links`, `.night_veil`,
    `.virtual_cells`, `hivemind.cli.stores`, `hivemind.common.secrets` (the Hive's persisted
    signing key, for the Virtual side), `hivemind.entrance` (the relay and telemetry board `hive
    serve` binds), `hivemind.forage` (ForageCapacity), `hivemind.guard`, `hivemind.honey_store`,
    `hivemind.llm`, `hivemind.manifest`, `hivemind.queen` (and `hivemind.queen.deps` for
    DispatchBook, GrantWaits and LiveCapacity), `hivemind.wardens`,
    `hivemind.workers.roles.house_bee` and waggle only.

Key invariants:
    - `build_hive` never touches the network: every provider it constructs is lazy
      (`hivemind.llm.registry.ProviderRegistry.provider`'s own rule), and its own `asyncio.run`
      calls only probe this host's own capacity (`hivemind.cell.local.HiveStandSource.cells`, to
      seed the Queen<->Warden link's Cell) and, with a Virtual side, read or mint the Hive's
      signing key in the local secret store (`_virtual_side`).
    - The Queen and the Hive Stand's Warden share one untrusted-content scanner (roadmap 10.6b),
      built from `[guard.untrusted_content]` and keyed from the secret store at `[hive]
      secrets_dir`; its key is minted on the first flag, never at build time.
    - A Hive with no Honey Store (a hand-built HiveStores) has no House Bee ripening loop.

See Also:
    - hivemind.cli.compose.deps for every manifest-to-deps builder `build_hive` composes.
    - hivemind.cli.compose.links for build_hive_links, the one Queen<->Warden wire.
    - hivemind.cli.compose.hive.run for run_hive, which starts what this module builds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace

from hivemind.cell.local import HiveStandSource
from hivemind.cli.compose.deps import (
    HiveParts,
    HiveStores,
    build_enforcer,
    build_fanner,
    build_hive_stand_source,
    build_ledger,
    build_queen_deps,
    build_warden_deps,
    open_default_stores,
)
from hivemind.cli.compose.guard import build_content_scanner, with_guard
from hivemind.cli.compose.honey import build_honey_access
from hivemind.cli.compose.links import HiveLinks, build_hive_links
from hivemind.cli.compose.night_veil import attach_side_channels, veil_trail
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.cli.stores import build_forage_map, build_provider_registry
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.entrance.notify import HumanChannelRelay
from hivemind.entrance.streams import TelemetryBoard
from hivemind.forage import ForageCapacity, ForageMap
from hivemind.guard import Enforcer, GuardRequestDoor
from hivemind.honey_store import HoneyAccess
from hivemind.llm import Fanner, ProviderRegistry, Responder
from hivemind.manifest import HiveManifest
from hivemind.queen import ForageLedger, Queen, QueenDeps, WardenLink
from hivemind.queen.deps import DispatchBook, GrantWaits, LiveCapacity
from hivemind.wardens import Warden, WardenDeps
from hivemind.workers.roles.house_bee import HouseBeeRipening
from waggle.clock import Clock

__all__ = ["Hive", "build_hive"]


@dataclass(frozen=True, slots=True)
class Hive:
    """Everything `hive run` -- and every test exercising the same kernel -- need a handle on.

    Attributes:
        manifest: The loaded HiveManifest this Hive was composed from.
        stores: The trail, chamber and memory store this Hive shares one SQLite file for.
        registry: Every configured model provider, lazily constructed and cached.
        fanner: The seat meter every model call in this Hive passes through.
        source: The Hive Stand's own RealCellSource; its one Cell is leased once `run_hive` starts.
        warden: The Hive Stand's own Warden, built but not yet started.
        queen: The Queen, not yet attached to `warden_link`: attaching is her `warden_spawn`
            enforcement point, an awaited Guard check, so `run_hive` does it (roadmap step 10.3).
        warden_link: The Queen's own end of the Queen<->Warden link; `run_hive` attaches it first
            and closes it on exit.
        clock: The injected time source every collaborator above shares.
        enforcer: The Hive's one Guard Enforcer, the Queen's and the Hive Entrance's (roadmap
            step 10.5: `hive serve` hands it to the Entrance's route enforcement point).
        human_channel: The Queen's HumanChannel: a relay that drops every call until `hive
            serve` binds it to the Hive Entrance's push channel (`hive run` never does, which
            is QueenDeps' own no-op default in effect).
        queen_deps: The deps the Queen was built with: `hive serve` hands the Entrance her Forage
            ledger, slot bindings, Clustering state and health poller from them, to read only.
        telemetry: Every Heartbeat the Queen records (her `on_heartbeat` hook is its `record`),
            for the Entrance's telemetry view; `hive run` leaves it unread.
        virtual_cells: `hivemind.cli.compose.virtual_cells.build_virtual_cells`'s own return
            value, when `[virtual_cells] backend` is set; `None` otherwise, in which case
            `run_hive` touches nothing Virtual-Cell-related at all (roadmap step 5.6).
        honey: The Honey Store's handles, the same ones the Queen holds as `QueenDeps.honey`;
            None when the stores carry no Honey Store (roadmap phase 7).
        house_bee: The House Bee's ripening loop over `honey`, run by `run_hive`; None exactly
            when `honey` is.
    """

    manifest: HiveManifest
    stores: HiveStores
    registry: ProviderRegistry
    fanner: Fanner
    source: HiveStandSource
    warden: Warden
    queen: Queen
    warden_link: WardenLink
    clock: Clock
    enforcer: Enforcer
    human_channel: HumanChannelRelay
    queen_deps: QueenDeps
    telemetry: TelemetryBoard
    virtual_cells: VirtualCellsParts | None = None
    honey: HoneyAccess | None = None
    house_bee: HouseBeeRipening | None = None

    @property
    def guard_door(self) -> GuardRequestDoor:
        """The Queen's door for a Guard request: the running Queen herself (roadmap step 10.6a).

        The Guard Bee (roadmap step 10.6) is handed this and files every request through it; the
        request is durable before `file_guard_request` returns and decided on her next tick.
        """
        return self.queen


def build_hive(
    manifest: HiveManifest,
    *,
    environ: Mapping[str, str],
    clock: Clock,
    stores: HiveStores | None = None,
    responders: Mapping[str, Responder] | None = None,
) -> Hive:
    """Turn a loaded Hive Manifest into a running Hive's every collaborator, not yet started.

    The one place a HiveManifest is converted into deps (codingrules section 13): every subsystem
    below `cli` takes only the slice `hivemind.cli.compose.deps`'s builders carve from it. Never
    awaits a model or opens a network connection: every provider is built lazily, and the
    `asyncio.run` calls (`_build_links`, `_virtual_side`) only probe this host and read secrets.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        environ: The composition root's own environment mapping, read once for provider API keys.
        clock: Injected time source shared by every collaborator this builds.
        stores: A test's own in-memory `HiveStores`; `open_default_stores(manifest)` (real
            SQLite, `[hive] db`) when omitted.
        responders: Installed on every `kind = "fake"` provider this Hive constructs
            (`hivemind.cli.stores.build_provider_registry`); `None` in production.

    Returns:
        A Hive not yet started (no lease, no tick, no Warden attached); pass it to `run_hive`.
    """
    # Codingrules 12: every writer below records through the Night Veil boundary, one per Hive.
    hive_stores = _veiled_stores(manifest, stores, clock)
    forage_map = build_forage_map(manifest, clock)
    # Roadmap step 4.8: built before build_fanner, whose LedgerRecorder books each llm.call live.
    ledger = build_ledger(manifest, manifest.forage.reserve)
    registry = build_provider_registry(manifest, environ, clock, forage_map, responders)
    fanner = build_fanner(manifest, forage_map, hive_stores.trail, clock, ledger)
    source = build_hive_stand_source(manifest, hive_stores.trail, clock, hive_stores.leavings)
    links = _build_links(manifest, source, clock)
    virtual_cells = _virtual_side(manifest, hive_stores, ledger, clock, environ)  # Roadmap 5.6.
    parts = HiveParts(
        manifest=manifest,
        registry=registry,
        fanner=fanner,
        stores=hive_stores,
        clock=clock,
        enforcer=build_enforcer(manifest, hive_stores.trail, clock),  # Roadmap step 10.3.
    )
    extras = _AssemblyExtras(
        forage_map=forage_map, ledger=ledger, virtual_cells=virtual_cells, honey=_honey(parts)
    )
    return _assemble_hive(parts, source, links, extras)


def _honey(parts: HiveParts) -> HoneyAccess | None:
    """Build the Honey Store's handles once, when the stores carry one (roadmap phase 7).

    Every handle the Queen and the House Bee use comes from this one build, the same way the
    `hive honey` commands build theirs (`hivemind.cli.compose.honey.build_honey_access`).
    """
    store = parts.stores.honey
    if store is None:
        return None  # A hand-built HiveStores (a test's in-memory stores): no Honey Store.
    return build_honey_access(parts.manifest, store, parts.registry, parts.fanner, parts.clock)


def _veiled_stores(manifest: HiveManifest, stores: HiveStores | None, clock: Clock) -> HiveStores:
    """Open (or take a test's) stores, their trail wrapped in the Night Veil boundary.

    `hivemind.cli.compose.night_veil.veil_trail` wraps it only when a Virtual side is configured,
    since only a Virtual Cell can be Night Veil; `build_virtual_cells` then builds the rest of the
    boundary around the same trail, so the Queen and the Virtual side share one set of segments.
    """
    opened = stores if stores is not None else open_default_stores(manifest)
    return replace(opened, trail=veil_trail(manifest, opened.trail, clock))


def _build_links(manifest: HiveManifest, source: HiveStandSource, clock: Clock) -> HiveLinks:
    """Probe the Hive Stand's one Cell and build the Queen<->Warden link around it.

    The Queen-side link also carries the reader of the Hive Stand's capacity as it stands (the
    zero-grant fix): the Cell probed here is a snapshot of this moment, while every grant the
    Queen sizes for the Hive Stand must see its load and free memory as they are then.

    SAFETY: a fresh event loop for this one setup call, the seam where a sync composition-root
    function first reaches `HiveStandSource.cells` (an async `RealCellSource` method that here
    does no real I/O: it only reads this host's own already-probed capacity), mirroring
    `hivemind.cli.stores.open_trail`'s own `asyncio.run` seam (codingrules section 8.2).
    """
    cell = asyncio.run(source.cells())[0]
    links = build_hive_links(manifest.hive.id, manifest.hive.node_id, cell, clock)
    queen_link = replace(links.queen_link, live_capacity=_hive_stand_capacity(source))
    return replace(links, queen_link=queen_link)


def _hive_stand_capacity(source: HiveStandSource) -> LiveCapacity:
    """Return the reader of the Hive Stand's own capacity as it stands, for its Queen-side link."""

    async def read() -> ForageCapacity:
        """Read the Hive Stand's capacity now: static totals, live load and free figures."""
        # HiveStandSource.cells() re-reads the load average, free memory and free disk on every
        # call (hivemind.cell.local.probe.refresh_live): a handful of fast system reads, no I/O
        # worth a timeout.
        return (await source.cells())[0].capacity

    return read


def _virtual_side(
    manifest: HiveManifest,
    stores: HiveStores,
    ledger: ForageLedger,
    clock: Clock,
    environ: Mapping[str, str],
) -> VirtualCellsParts | None:
    """Build the Virtual side, signed with the Hive's own key, its Night Veil purge fully wired.

    Roadmap step 5.6: None when `[virtual_cells] backend` is unset (`build_virtual_cells`'s own
    docstring), and a Hive with no Virtual side never writes a key it does not use. Phase 5 open
    item 5: the Queen signs every Virtual Cell frame with the Hive's key, so it must be the same
    key after a restart; it lives in the secret store at the manifest's resolved `[hive]
    secrets_dir` (a test's manifest lives under its own `tmp_path`, so its key does too), minted
    on the Hive's first run. Codingrules 12: the boundary's purge clears the Queen's memory
    tables, the Brood Chamber, the Forage ledger and the snapshot images of a Night Veil Cell too
    (`attach_side_channels`).

    SAFETY: a fresh event loop for this one setup call, the same seam `_build_links` uses: the
    secret store is async, `build_hive` is a sync composition root, and the call is one small
    file read (or one write, the first time), never a network request.
    """
    signer = None
    if manifest.virtual_cells.backend is not None:
        secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
        signer = asyncio.run(load_or_mint_hive_signer(secrets))
    parts = build_virtual_cells(manifest, stores.trail, clock, environ, hive_signer=signer)
    if parts is not None:
        attach_side_channels(parts.night_veil, parts.registry, stores, ledger)
    return parts


@dataclass(frozen=True, slots=True)
class _AssemblyExtras:
    """Builder outputs `_assemble_hive` needs beyond `parts`/`source`/`links` (codingrules 5.1)."""

    forage_map: ForageMap
    ledger: ForageLedger
    virtual_cells: VirtualCellsParts | None
    honey: HoneyAccess | None  # The Honey Store's handles (roadmap phase 7); None without one.


def _assemble_hive(
    parts: HiveParts, source: HiveStandSource, links: HiveLinks, extras: _AssemblyExtras
) -> Hive:
    """Build the Warden, Queen and House Bee from `parts` and wrap them; `run_hive` attaches."""
    # Roadmap 10.6b: one configured scanner, shared by the Queen and the Hive Stand's Warden,
    # replaces each deps bundle's shipped-default one, so every flag here is hashed under one key.
    scanner = build_content_scanner(parts.manifest)
    warden_deps = replace(build_warden_deps(parts, source, links), scanner=scanner)
    warden = Warden(links.warden_id, warden_deps)
    side = _build_queen(parts, extras, warden_deps)
    queen = side.queen
    if extras.virtual_cells is not None:
        # Safe before run_hive/listener.start(): acquire() is only ever called from a tick, well
        # after both are running (hivemind.queen.cell_gate.provider's own module docstring).
        extras.virtual_cells.provider.bind_queen(queen)
    return Hive(
        manifest=parts.manifest,
        stores=parts.stores,
        registry=parts.registry,
        fanner=parts.fanner,
        source=source,
        warden=warden,
        queen=queen,
        warden_link=links.queen_link,
        clock=parts.clock,
        enforcer=parts.enforcer,
        human_channel=side.relay,
        queen_deps=side.deps,
        telemetry=side.telemetry,
        virtual_cells=extras.virtual_cells,
        honey=extras.honey,
        house_bee=_house_bee(extras.honey, links, parts.clock),
    )


@dataclass(frozen=True, slots=True)
class _QueenSide:
    """The Queen and the two edges `hive serve` binds beside her, built together (codingrules 5.1).

    Attributes:
        queen: The Queen, her Guard door already bound to her.
        deps: The deps she was built with (`Hive.queen_deps`).
        relay: Her HumanChannel relay (`Hive.human_channel`).
        telemetry: The board her `on_heartbeat` hook feeds (`Hive.telemetry`).
    """

    queen: Queen
    deps: QueenDeps
    relay: HumanChannelRelay
    telemetry: TelemetryBoard


def _build_queen(parts: HiveParts, extras: _AssemblyExtras, warden_deps: WardenDeps) -> _QueenSide:
    """Build the Queen on the Warden's own scanner, with her Honey handles and the Entrance's edges.

    Split out of `_assemble_hive` for its line budget (codingrules 5.1).
    """
    queen_deps = build_queen_deps(
        parts, extras.forage_map, extras.ledger, extras.virtual_cells, extras.honey
    )
    # Roadmap step 10.5: the Queen tells devices through a relay `hive serve` binds to the
    # Entrance's push channel once that exists (the Entrance is built inside the event loop).
    relay = HumanChannelRelay()
    # Roadmap step 10.5: Heartbeats never reach the trail, so the Entrance's telemetry view
    # follows them through this board, which the Queen feeds from her first tick.
    telemetry = TelemetryBoard()
    # The zero-grant fix: how long a fresh task may wait for its Cell's live figures to make room.
    waits = GrantWaits(patience_s=parts.manifest.forage.zero_grant_patience_s)
    queen_deps = replace(
        queen_deps,
        scanner=warden_deps.scanner,  # Roadmap 10.6b: the one scanner the Warden holds too.
        human_channel=relay,
        on_heartbeat=telemetry.record,
        dispatch=DispatchBook(waits=waits),
    )
    # Roadmap steps 10.6a and 10.6: her Guard request side, and the Guard Bee filing through her.
    lifecycle = extras.virtual_cells.lifecycle if extras.virtual_cells is not None else None
    queen_deps, door = with_guard(parts.manifest, queen_deps, lifecycle, warden_deps.tiers)
    queen = Queen(queen_deps)
    door.bind(queen)  # Before anything ticks: she is the Guard Bee's door (Hive.guard_door).
    return _QueenSide(queen=queen, deps=queen_deps, relay=relay, telemetry=telemetry)


def _house_bee(
    honey: HoneyAccess | None, links: HiveLinks, clock: Clock
) -> HouseBeeRipening | None:
    """Build the House Bee's ripening loop over `honey`; None for a Hive with no Honey Store."""
    if honey is None:
        return None
    # The operator's proposed notes are attributed to the Hive Stand: the machine the Queen, and
    # so the operator's own CLI, runs on (hivemind.workers.roles.house_bee.loop).
    return HouseBeeRipening(honey, links.queen_link.cell.id, clock)
