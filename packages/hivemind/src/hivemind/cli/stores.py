"""Open the Hive's stores, and convert a loaded Hive Manifest into hivemind.llm's own inputs.

This is the CLI's one composition root for two related jobs. The first, from phase 2: `open_trail`
and `open_chamber` are plain synchronous functions, each running its own `asyncio.run` internally,
that open a database file and hand back a ready `SqlitePheromoneTrail`/`BroodChamber` (a typer
command body is sync, so this is the seam where the CLI first steps into the async code every
store's `create` classmethod needs, codingrules section 8.2). `DbOption` is the shared `--db` typer
annotation both `hivemind.cli.tasks` and `hivemind.cli.trail` attach to their own commands. The
second, from phase 3 step 3.21: `hivemind.llm` may not import `hivemind.manifest` (codingrules
section 4's Layer 1 "llm | manifest" independent siblings), so something above both packages has to
turn a loaded `HiveManifest` into the Forage-side and registry-side shapes `hivemind.llm.slots.
resolve` and `hivemind.llm.registry.ProviderRegistry` actually take. `slot_bindings` and
`provider_configs` are that conversion (the same one `tests/builders/llm.py`'s
`bindings_from_manifest`/`provider_configs_from_manifest` prototyped for tests, now shipped as the
real thing); `build_forage_map` does the matching conversion for `[forage.map]`; `build_registry`
composes all three into one ready `ProviderRegistry`. `hive llm` was the first caller;
`hivemind.cli.compose.build_hive` (roadmap step 3.21, second half) is the next, and adds
`open_memory` (a `MemoryStore` opened the same way as `open_trail`/`open_chamber`) plus two
optional `build_registry` keyword arguments (`factories`, `forage_map`) it needs to share one
`ForageMap` between the registry, the Fanner and `QueenDeps.map`, and to substitute a
responder-installing `"fake"` factory for `hive run`'s own tests.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.tasks` and
    `hivemind.cli.trail` (the store functions) and by `hivemind.cli.llm` (the manifest-conversion
    functions), and by every later CLI step that needs a live `ProviderRegistry`. Calls into
    `hivemind.brood_chamber`, `hivemind.common.sqlite`, `hivemind.pheromone`, `hivemind.forage`,
    `hivemind.llm` and `hivemind.manifest`.

Key invariants:
    - `open_chamber` always applies the Pheromone Trail's migrations on its own connection before
      building the `SqliteTaskStore`, so `SqliteTaskStore.create`'s own loud-failure check (no
      `pheromone_events` table) never fires from a CLI-opened database.
    - `open_trail` and `open_chamber` each open their own `sqlite3.Connection` to `db`; two stores
      that write the same file use separate connections (WAL makes that fine, ADR-0006 decision
      1), so a `hive tasks` and a `hive trail` command run one after another never share state.
    - `provider_configs` derives `ProviderConfig.default_model` from every provider's first
      `[llm.slots]` row, in manifest table order, regardless of that provider's `kind` -- the same
      rule `hivemind.llm.registry.MissingDefaultModelError`'s docstring documents as the
      composition root's job. Only `_build_openai_compat` (`hivemind.llm.registry`) ever reads the
      field; every other kind's factory carries a populated-but-unused value.
    - `build_forage_map` gives every `[forage.map.*]` source an initial `Abundance` of its own
      declared `seats`, since a freshly loaded manifest has never been measured yet; the Fanner
      (`hivemind.llm.fanner`) is the only thing that ever calls `ForageMap.observe`/
      `set_abundance` afterwards.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the "separate connections" decision.
    - .claude/codingrules.md section 4 for the Layer 1 "llm | manifest" independence this module
      exists to bridge.
    - hivemind.brood_chamber.chamber for BroodChamber and ChamberIdentity.
    - hivemind.pheromone.trail.sqlite for SqlitePheromoneTrail.create, applied by both store
      functions here.
    - hivemind.llm.registry for ProviderRegistry, ProviderConfig, RegistryDeps and
      default_factories, the shapes build_registry composes.
    - hivemind.forage.map for ForageMap and SlotBinding, the shapes build_forage_map and
      slot_bindings build.
    - tests/builders/llm.py for the test-only prototype these four functions now replace in
      production; that module's own bindings_from_manifest/provider_configs_from_manifest now call
      straight through to slot_bindings/provider_configs.

Public API:
    - DEFAULT_DB: the database file every command falls back to.
    - DbOption: the shared `--db` typer option annotation.
    - DEFAULT_MANIFEST, ManifestOption, JsonOption: the shared `--manifest`/`--json` typer option
      annotations every command group from roadmap step 3.21 on attaches.
    - load_manifest_or_exit: load a manifest or exit 2 with `ManifestError`'s own message.
    - open_trail, open_chamber, open_memory: the three store composition functions.
    - build_registry, slot_bindings, provider_configs, build_forage_map: the manifest-to-llm
      conversion functions.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

import typer

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, SqliteTaskStore
from hivemind.common.sqlite import connect
from hivemind.forage import Abundance, ForageMap, ModelSource
from hivemind.forage.map import SlotBinding
from hivemind.llm import (
    ProviderConfig,
    ProviderFactory,
    ProviderKind,
    ProviderRegistry,
    RegistryDeps,
    default_factories,
)
from hivemind.manifest import HiveManifest, ManifestError, load_manifest
from hivemind.memory import MemoryStore, SqliteMemoryStore
from hivemind.pheromone import SqlitePheromoneTrail
from waggle.clock import Clock, SystemClock

# A single file in the current directory: good enough before the Hive Manifest (phase 3) names
# one explicitly.
DEFAULT_DB = Path("hive.sqlite3")

# Shared so `hive tasks` and `hive trail` declare `--db` with the exact same flag and help text;
# each command still supplies its own default (DEFAULT_DB) at the parameter, since a typer.Option
# built once at import time cannot see which command it will end up decorating.
DbOption = Annotated[
    Path, typer.Option("--db", help=f"The Hive's SQLite database file (default: {DEFAULT_DB}).")
]

# Every command group added from roadmap step 3.21 on takes a Hive Manifest; `hive llm` (3.21's
# first half) defined its own copy of this pair locally before `hive run`/`cells`/`inbox`/`wardens`
# (3.21's second half) existed to share it with, so this is that consolidation (this module's own
# docstring flags it as optional; done here since four more command groups would otherwise repeat
# it a fifth time).
DEFAULT_MANIFEST = Path("hive.toml")
ManifestOption = Annotated[
    Path,
    typer.Option("--manifest", help=f"The Hive Manifest TOML file (default: {DEFAULT_MANIFEST})."),
]
# Likewise shared by every command group that offers `--json` (`hive llm`, `hive capping`, and now
# `hive cells`/`inbox`/`wardens`).
JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")]

__all__ = [
    "DEFAULT_DB",
    "DEFAULT_MANIFEST",
    "DbOption",
    "JsonOption",
    "ManifestOption",
    "build_forage_map",
    "build_registry",
    "load_manifest_or_exit",
    "open_chamber",
    "open_memory",
    "open_trail",
    "provider_configs",
    "slot_bindings",
]


def open_trail(db: Path) -> SqlitePheromoneTrail:
    """Open `db` and return a ready SqlitePheromoneTrail, applying its migrations first.

    Args:
        db: The Hive's SQLite database file.

    Returns:
        A SqlitePheromoneTrail whose `pheromone_events` table exists and is current.
    """

    async def _open() -> SqlitePheromoneTrail:
        connection = connect(db)
        return await SqlitePheromoneTrail.create(connection, SystemClock())

    # asyncio.run: a fresh event loop for this one setup call, the seam where a sync typer command
    # body first reaches the async store layer (codingrules section 8.2).
    return asyncio.run(_open())


def open_chamber(db: Path, identity: ChamberIdentity) -> BroodChamber:
    """Open `db` and return a ready BroodChamber, applying both subsystems' migrations first.

    Args:
        db: The Hive's SQLite database file.
        identity: The hive, node and actor every TaskEvent this chamber writes is stamped with.

    Returns:
        A BroodChamber backed by a SqliteTaskStore whose tables exist and are current.
    """

    async def _open() -> BroodChamber:
        connection = connect(db)
        clock = SystemClock()
        # SqliteTaskStore.create refuses to proceed without a pheromone_events table already on
        # its connection, so the trail's own migration runs first, on this same connection, on
        # every call -- a fresh database file is never a problem here.
        await SqlitePheromoneTrail.create(connection, clock)
        store = await SqliteTaskStore.create(connection, clock)
        return BroodChamber(store, clock, identity)

    return asyncio.run(_open())


def open_memory(db: Path) -> MemoryStore:
    """Open `db` and return a ready MemoryStore, applying both subsystems' migrations first.

    Added in roadmap step 3.21 (second half) so `hive inbox answer` (a separate process from a
    running `hive run`, v0 has no live link into the Queen process) can leave a human's answer
    text somewhere `hivemind.queen.questions.sync_answers_from_chamber` can read it back: the
    Brood Chamber's own public API exposes no way to read an already-`ANSWERED` Question's text
    (`hivemind.queen.questions`'s own module docstring), but `hivemind.memory.MemoryStore.
    list_notes` can be filtered by `author`, so `hive inbox answer` also writes a Note keyed by
    the question's id.

    Args:
        db: The Hive's SQLite database file.

    Returns:
        A SqliteMemoryStore whose four tables exist and are current.
    """

    async def _open() -> MemoryStore:
        connection = connect(db)
        clock = SystemClock()
        # Same "trail's migration runs first" rule open_chamber follows: SqliteMemoryStore.create
        # refuses without a pheromone_events table already on this connection.
        await SqlitePheromoneTrail.create(connection, clock)
        return await SqliteMemoryStore.create(connection, clock)

    return asyncio.run(_open())


def load_manifest_or_exit(path: Path) -> HiveManifest:
    """Load the Hive Manifest at `path`, or print `ManifestError`'s message and exit 2.

    The same bad-input shape `hivemind.cli.tasks`/`.trail` already give a malformed input
    (codingrules section 10: a CLI command body is one of the three places a broad-looking catch
    is allowed); `hivemind.cli.llm`'s own `_load_manifest_or_exit` predates this consolidation and
    keeps its own private copy rather than being edited to call this one (not this dispatch's file
    to change without cause).

    Args:
        path: A `--manifest` option's own value.

    Returns:
        The loaded HiveManifest.

    Raises:
        typer.Exit: Always, with code 2, when `path` does not exist or fails validation; the
            command that called this never sees a raw `ManifestError` or a traceback.
    """
    try:
        # SAFETY: top of a CLI command's own input-loading step (codingrules section 10): a
        # missing file, invalid TOML or a validation failure all become one clean stderr line.
        # os.environ is read here, at the composition root's very edge, and passed down; nothing
        # below this line reads it again (codingrules section 13).
        return load_manifest(path, environ=os.environ)
    except ManifestError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc


def slot_bindings(manifest: HiveManifest) -> tuple[SlotBinding, ...]:
    """Convert `manifest`'s `[llm.slots]` table into forage-side SlotBinding rows.

    `hivemind.llm.slots.resolve`/`resolve_key` walk `hivemind.forage.map.SlotBinding`, not the
    manifest's own `hivemind.manifest.schema.llm.SlotBinding` (see this module's docstring for why
    `hivemind.llm` cannot do this conversion itself).

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        Every `[llm.slots]` row, forage-side, in manifest table order.
    """
    return tuple(
        SlotBinding(
            key=key,
            provider=row.provider,
            model=row.model,
            fallback=row.fallback,
            effort=row.effort,
        )
        for key, row in manifest.llm.slots.items()
    )


def provider_configs(manifest: HiveManifest) -> Mapping[str, ProviderConfig]:
    """Convert `manifest`'s `[llm.providers]` table into ProviderRegistry-facing ProviderConfigs.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.

    Returns:
        Every `[llm.providers]` row, keyed by its own manifest name, as a ProviderConfig; each
        one's `default_model` is that provider's first `[llm.slots]` model id, regardless of kind
        (see this module's Key invariants) -- `None` only for a configured provider no slot binds.
    """
    default_models = _first_model_by_provider(manifest)
    return {
        name: ProviderConfig(
            kind=spec.kind,
            base_url=spec.base_url,
            api_key_env=spec.api_key_env,
            timeout_s=spec.timeout_s,
            capability_overrides=spec.capabilities.as_overrides(),
            default_model=default_models.get(name),
        )
        for name, spec in manifest.llm.providers.items()
    }


def build_forage_map(manifest: HiveManifest, clock: Clock) -> ForageMap:
    """Convert `manifest`'s `[forage.map]` table into a live ForageMap.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        clock: Passed through to the built ForageMap, which only reads it when a later caller
            (the Fanner) records a fresh measurement through `observe`.

    Returns:
        A ForageMap with one ModelSource per `[forage.map.<source_id>]` entry, each starting with
        no measured Distance and an Abundance of its own declared `seats` (see this module's Key
        invariants).
    """
    sources = (
        ModelSource(source_id=source_id, spec=spec, abundance=Abundance(seats_free=spec.seats))
        for source_id, spec in manifest.forage.map.items()
    )
    return ForageMap(sources, clock=clock)


def build_registry(
    manifest: HiveManifest,
    environ: Mapping[str, str],
    clock: Clock,
    *,
    factories: Mapping[ProviderKind, ProviderFactory] | None = None,
    forage_map: ForageMap | None = None,
) -> ProviderRegistry:
    """Build a ready ProviderRegistry from a loaded HiveManifest.

    Composes `provider_configs`, `slot_bindings` and `build_forage_map` with the built-in adapter
    factories (`hivemind.llm.registry.default_factories`) into one `RegistryDeps`; no provider is
    actually constructed until a caller resolves a slot or asks for one by name (`hivemind.llm.
    registry.ProviderRegistry.provider`'s own lazy-construction rule).

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        environ: A raw environment mapping to read provider API keys from (codingrules section
            13); the composition root's own `os.environ`, never read by this function itself.
        clock: Passed to the built ForageMap and to every provider this registry later constructs.
        factories: Overrides `hivemind.llm.registry.default_factories()`; added in roadmap step
            3.21 (second half) so `hivemind.cli.compose.build_hive` can substitute a `"fake"`
            factory that installs a scripted `hivemind.llm.Responder` on every `FakeLLMProvider`
            it builds (this module's own docstring's "not a kind branch" note: the substitute
            factory is still selected by `ProviderConfig.kind`, the same dispatch
            `hivemind.llm.registry.default_factories` already performs). `None` keeps today's
            behaviour.
        forage_map: A ForageMap to share with this registry's `BoundModel` pricing, instead of a
            fresh one `build_forage_map` would otherwise build; added so `build_hive` can hand the
            same live map to the registry, the Fanner and `QueenDeps.map` (one Hive, one map).
            `None` keeps today's behaviour (`build_forage_map(manifest, clock)`).

    Returns:
        A ProviderRegistry ready to resolve any `[llm.slots]` binding this manifest declares.
    """
    deps = RegistryDeps(
        factories=factories if factories is not None else default_factories(),
        environ=environ,
        clock=clock,
        map=forage_map if forage_map is not None else build_forage_map(manifest, clock),
    )
    return ProviderRegistry(
        provider_configs(manifest), slot_bindings(manifest), manifest.llm.offline, deps
    )


def _first_model_by_provider(manifest: HiveManifest) -> dict[str, str]:
    """Return the first `[llm.slots]` row's model id seen for each provider name, in table order.

    The one rule `provider_configs` needs to fill in `ProviderConfig.default_model` for a
    `kind="openai_compat"` row, which has no manifest-level default of its own (see
    `hivemind.llm.registry.MissingDefaultModelError`'s docstring).
    """
    defaults: dict[str, str] = {}
    for row in manifest.llm.slots.values():
        defaults.setdefault(row.provider, row.model)
    return defaults
