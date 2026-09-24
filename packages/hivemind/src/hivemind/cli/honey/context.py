"""Share what every `hive honey` command needs: its options, its reader, and how to open the store.

`hive honey` is the operator's door into the Honey Store (the Hive's cold-tier knowledge base:
Nectar, raw findings, ripened into Honey, searchable knowledge). Its group callback keeps
`--manifest`, `--db` and `--clearance` (codingrules 5.1's parameter cap leaves a command no room
to repeat them) and `cli_context` loads the manifest once a command body runs, so a subcommand's
`--help` never needs one. The operator reads as
`operator_reader` (every scope, up to `--clearance`). Commands that call a model (`query`,
`ripen --now`, `reembed`, `review --judge`) open the Honey Store the one way the Hive does --
`open_honey_store`, then `build_honey_access` over a registry and Fanner built as
`hivemind.cli.compose` builds them -- and probe the EMBEDDER, RIPENER and JUDGE bindings so a
missing one is printed with its reason instead of failing; commands that only read or record
(`stats`, `ls`, `cat`, `propose`, `relabel`, `review` and its human decisions) open the store
alone and need no model binding at all.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `honey` command group. Imported
    by `hivemind.cli.honey.query`, `.browse`, `.maintain` and `.review`. Calls into
    `hivemind.cli.stores`, `hivemind.cli.compose`, `hivemind.honey_store`, `hivemind.forage`,
    `hivemind.llm` (only for the typed errors a binding probe catches) and `hivemind.manifest`.

Key invariants:
    - The Honey Store is only ever built through `open_honey_store` and `build_honey_access`.
    - Every error a command body reports is a typed `HiveMindError`, turned into one stderr line
      and a non-zero exit by `run_or_exit`; no command prints a traceback for one.
    - The JUDGE probe honours `[honey.lowering] enabled = false` exactly as `build_honey_access`
      does: the slot is reported as unused for that reason and never resolved.

See Also:
    - hivemind.cli.compose.honey for build_honey_access, the one way the Honey Store is built.
    - hivemind.honey_store.browse for HoneyBrowser and operator_reader.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cell import HoneyClearance
from hivemind.cli.compose.deps import build_fanner, build_provider_registry
from hivemind.cli.compose.honey import JUDGE_DISABLED, build_honey_access
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    DbOption,
    ManifestOption,
    build_forage_map,
    load_manifest_or_exit,
    open_honey_store,
    open_trail,
)
from hivemind.common.errors import HiveMindError
from hivemind.forage import ModelSlot
from hivemind.honey_store import HoneyAccess, HoneyIdentity, HoneyReader, LoweringInputError
from hivemind.honey_store.browse import BrowseInputError, BrowsePathError, operator_reader
from hivemind.llm import ProviderRegistry
from hivemind.manifest import HiveManifest, HoneyLoweringSection
from waggle.clock import SystemClock

HUMAN_ACTOR = "human"  # Every write these commands record is the operator's own act.
EXIT_REFUSED = 1  # The Hive refused or could not do what was asked.
EXIT_BAD_INPUT = 2  # The command line itself was wrong (a path, a title), as typer's own usage.
# What the operator typed and the Honey Store refused as input, not as a decision: exit 2.
_BAD_INPUT_ERRORS = (BrowsePathError, BrowseInputError, LoweringInputError)

# `--clearance`, shared by every reading command through the group callback.
ClearanceOption = Annotated[
    HoneyClearance,
    typer.Option(
        "--clearance",
        help="The most sensitive label to read (C0, C1 or C2); applies to query, ls and cat.",
    ),
]

__all__ = [
    "EXIT_BAD_INPUT",
    "EXIT_REFUSED",
    "HUMAN_ACTOR",
    "BindingStatus",
    "ClearanceOption",
    "HoneyCliContext",
    "OpenedHoney",
    "SlotStatus",
    "cli_context",
    "honey_callback",
    "human_identity",
    "open_access",
    "reader_for",
    "run_or_exit",
]


@dataclass(frozen=True, slots=True)
class HoneyCliContext:
    """The manifest, database and reading ceiling every `hive honey` command shares."""

    manifest: HiveManifest  # The loaded Hive Manifest.
    db: Path  # The SQLite file holding this Hive's Honey Store.
    clearance: HoneyClearance  # The operator's reading ceiling (`--clearance`).


@dataclass(frozen=True, slots=True)
class _HoneyOptions:
    """The group's own options, as typed, before any file is read."""

    manifest: Path  # `--manifest`.
    db: Path | None  # `--db`, when given.
    clearance: HoneyClearance  # `--clearance`.


@dataclass(frozen=True, slots=True)
class SlotStatus:
    """One model slot as `hive honey` found it: the model it serves, or why it serves none."""

    model: str | None  # The bound model id; None when the slot cannot serve.
    reason: str | None  # Why it cannot serve; None when it can.


@dataclass(frozen=True, slots=True)
class BindingStatus:
    """The slots the Honey Store calls: EMBEDDER, RIPENER and the clearance judge on JUDGE."""

    embedder: SlotStatus  # Without it, rows get no vectors and search is full text only.
    ripener: SlotStatus  # Without it, summaries are heuristic.
    judge: SlotStatus  # Without it, every lowering proposal waits for the human (ADR-0034).


@dataclass(frozen=True, slots=True)
class OpenedHoney:
    """The Hive's Honey Store handles, and how its two model slots resolved."""

    access: HoneyAccess  # Built by build_honey_access, exactly as the running Hive builds it.
    bindings: BindingStatus  # For printing a missing slot's reason.
    registry: ProviderRegistry  # Closed by the command's model-calling run (closing_registry).


def honey_callback(
    ctx: typer.Context,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    db: DbOption = None,
    clearance: ClearanceOption = HoneyClearance.C2,
) -> None:
    """Keep `--manifest`, `--db` and `--clearance` for whichever `hive honey` subcommand runs."""
    # Only kept here, loaded by `cli_context` when a command body runs: `hive honey ls --help`
    # must answer on a machine with no manifest to hand, and a callback that loaded it could not.
    ctx.obj = _HoneyOptions(manifest=manifest, db=db, clearance=clearance)


def cli_context(ctx: typer.Context) -> HoneyCliContext:
    """Load the manifest the group's options name, and resolve the database file.

    Args:
        ctx: The subcommand's own typer context; its parent ran `honey_callback`.

    Returns:
        The shared manifest, database and reading ceiling.

    Raises:
        typer.Exit: Code 2, through `load_manifest_or_exit`, when the manifest is missing or
            fails validation.
    """
    options: _HoneyOptions = ctx.obj
    loaded = load_manifest_or_exit(options.manifest)
    # An explicit --db wins; otherwise the manifest's own [hive] db, beside the manifest.
    db_path = options.db if options.db is not None else loaded.resolve_path(loaded.hive.db)
    return HoneyCliContext(manifest=loaded, db=db_path, clearance=options.clearance)


def reader_for(cli_ctx: HoneyCliContext) -> HoneyReader:
    """Return the operator's reader: every scope, up to `--clearance`, named by the Hive's id.

    Args:
        cli_ctx: The shared context.

    Returns:
        The HoneyReader every query, listing and relabel of this invocation reads as.
    """
    return operator_reader(cli_ctx.manifest.hive.id, cli_ctx.clearance)


def human_identity(manifest: HiveManifest) -> HoneyIdentity:
    """Return the identity the operator's own writes (a note, a relabel, a review) are under.

    Args:
        manifest: The loaded Hive Manifest.

    Returns:
        This Hive and node, with the human as actor.
    """
    return HoneyIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=HUMAN_ACTOR)


def open_access(cli_ctx: HoneyCliContext) -> OpenedHoney:
    """Open the Hive's Honey Store with its model bindings, exactly as the running Hive does.

    Must run outside any event loop: every `open_*` here runs its own `asyncio.run`.

    Args:
        cli_ctx: The shared context.

    Returns:
        The HoneyAccess `build_honey_access` built (any slot may be missing, never an error),
        and each slot's model or the reason it has none.
    """
    manifest = cli_ctx.manifest
    clock = SystemClock()
    store = open_honey_store(cli_ctx.db)
    # One live Forage map shared by the registry and the Fanner, as hivemind.cli.compose does,
    # and the Fanner records every embedding, summary and judge call on the Hive's own trail.
    forage_map = build_forage_map(manifest, clock)
    registry = build_provider_registry(manifest, os.environ, clock, forage_map, None)
    fanner = build_fanner(manifest, forage_map, open_trail(cli_ctx.db), clock)
    access = build_honey_access(manifest, store, registry, fanner, clock)
    bindings = _probe_bindings(registry, manifest.honey.lowering)
    return OpenedHoney(access=access, bindings=bindings, registry=registry)


def run_or_exit[ResultT](work: Coroutine[object, None, ResultT]) -> ResultT:
    """Run one command's async work, turning a typed Hive error into a clean stderr line.

    Args:
        work: The command's coroutine.

    Returns:
        Whatever `work` returned.

    Raises:
        typer.Exit: EXIT_BAD_INPUT for a path, text or reason the Honey Store refused as input,
            EXIT_REFUSED for any other HiveMindError; the message goes to stderr, never as a
            traceback.
    """
    try:
        return asyncio.run(work)
    except _BAD_INPUT_ERRORS as exc:
        # The operator's own input (a path, a note, a review reason): usage, like typer's own.
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=EXIT_BAD_INPUT) from exc
    except HiveMindError as exc:
        # A typed refusal (not found, a label rule, a wax cap): one line, never a traceback.
        typer.echo(f"{exc.code}: {exc}", err=True)
        raise typer.Exit(code=EXIT_REFUSED) from exc


def _probe_bindings(registry: ProviderRegistry, lowering: HoneyLoweringSection) -> BindingStatus:
    """Resolve the three slots the way build_honey_access does, keeping why any one failed."""
    # Lowering switched off: the judge is never resolved, exactly as build_honey_access skips it.
    judge = (
        _probe(lambda: registry.bound(ModelSlot.JUDGE).model)
        if lowering.enabled
        else SlotStatus(model=None, reason=JUDGE_DISABLED)
    )
    return BindingStatus(
        embedder=_probe(lambda: registry.embedder(ModelSlot.EMBEDDER).model),
        ripener=_probe(lambda: registry.bound(ModelSlot.RIPENER).model),
        judge=judge,
    )


def _probe(resolve: Callable[[], str]) -> SlotStatus:
    """Return the model `resolve` binds, or the reason it cannot bind one."""
    try:
        return SlotStatus(model=resolve(), reason=None)
    except (HiveMindError, ValueError) as exc:
        # The same failures build_honey_access degrades on (ADR-0032: degrade, never fail
        # closed): an unsupported kind, an offline refusal, a provider its adapter refuses.
        code = getattr(exc, "code", type(exc).__name__)
        return SlotStatus(model=None, reason=f"{code}: {exc}")
