"""Share what every `hive basket` command needs: its `--manifest`, its stores and the deposit door.

`hive basket` is the operator's door into the Basket (the Hive's store of inert blobs: wheels,
archives, datasets, tool packages, kept by content hash and signed with the Hive key). `--manifest`
belongs to the group, as it does for `hive honey` (codingrules 5.1 leaves a command no room to
repeat it): it comes right after `basket` and before the subcommand (`hive basket --manifest
hive.toml list`). The callback only keeps the path; `cli_context` loads the manifest once a command
body runs, so a subcommand's `--help` never needs one. Commands that only read or queue open the
Basket store alone (`open_store`); `deposit` needs the Hive's whole deposit door, so `open_lab`
builds it the one way the Hive does -- `build_royal_jelly` over the Hive's stores and a Honey access
from the Honey group's `open_access` -- because every deposit writes a Honey record through the
Honey port, and `show` checks a manifest's signature against the same Hive key. A blob is named by
its full sha256 or any unambiguous prefix of it (`list` prints the first twelve digits).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `basket` command group. Imported
    by `hivemind.cli.basket.listing`, `.deposit` and `.lifecycle`. Calls into `hivemind.basket`,
    `hivemind.cli.compose`, `hivemind.cli.honey.context` (the Honey access), `hivemind.cli.stores`,
    `hivemind.cli.tools.context` (the exit codes the two groups share), `hivemind.manifest` and
    `hivemind.royal_jelly` only.

Key invariants:
    - The Basket store is only ever opened through `open_basket_store`, with the byte tree where
      the Hive's own composition puts it (`[basket] root`, else `basket/` beside the database).
    - The Lab is only ever built through `build_royal_jelly`, exactly as the running Hive builds
      it; its model registry is closed in the same event loop as the deposit that used it.
    - A prefix that names no blob, or two, is an error, never a guess.

See Also:
    - hivemind.cli.compose.royal_jelly for build_royal_jelly, the one way the Lab is built.
    - hivemind.cli.honey.context for open_access, the Honey Store handles the Honey port writes to.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from hivemind.basket import BasketStore, BlobNotFoundError, SqliteBasketStore
from hivemind.cell import HoneyClearance
from hivemind.cli.compose.deps import open_default_stores
from hivemind.cli.compose.royal_jelly import build_royal_jelly
from hivemind.cli.honey.context import HoneyCliContext, open_access
from hivemind.cli.stores import DEFAULT_MANIFEST, load_manifest_or_exit, open_basket_store
from hivemind.cli.tools.context import EXIT_REFUSED
from hivemind.llm import ProviderRegistry
from hivemind.manifest import HiveManifest
from hivemind.pheromone import PheromoneTrail
from hivemind.royal_jelly import RoyalJellyAccess
from waggle.clock import SystemClock

__all__ = [
    "BasketCliContext",
    "OpenedLab",
    "basket_callback",
    "cli_context",
    "open_lab",
    "open_store",
    "resolve_sha256",
]

SHA256_CHARS = 64  # A full hex digest; anything shorter is a prefix to resolve.
_MANIFEST_HELP = f"The Hive Manifest TOML file (default: {DEFAULT_MANIFEST})."
_WIDEST = HoneyClearance.C2  # The Honey access the deposit door writes through reads nothing here.


@dataclass(frozen=True, slots=True)
class BasketCliContext:
    """The manifest and database every `hive basket` command shares."""

    manifest: HiveManifest  # The loaded Hive Manifest.
    db: Path  # The SQLite file holding this Hive's Basket metadata, registry and trail.


@dataclass(frozen=True, slots=True)
class _Options:
    """The group's own option, as typed, before any file is read."""

    manifest: Path  # `--manifest`.


@dataclass(frozen=True, slots=True)
class OpenedLab:
    """The Hive's Royal Jelly handles for one deposit, and what to close afterwards."""

    access: RoyalJellyAccess  # Built by build_royal_jelly, exactly as the running Hive builds it.
    trail: PheromoneTrail  # The Hive's trail, where a deposit's events are recorded.
    registry: ProviderRegistry  # Closed by the command's run (`closing_registry`).


def basket_callback(
    ctx: typer.Context,
    manifest: Annotated[Path, typer.Option("--manifest", help=_MANIFEST_HELP)] = DEFAULT_MANIFEST,
) -> None:
    """Keep `--manifest` for whichever `hive basket` subcommand runs."""
    # Only kept here, loaded by `cli_context` when a command body runs: `hive basket list --help`
    # must answer on a machine with no manifest to hand, and a callback that loaded it could not.
    ctx.obj = _Options(manifest=manifest)


def cli_context(ctx: typer.Context) -> BasketCliContext:
    """Load the manifest the group's option names, and resolve the database file.

    Args:
        ctx: The subcommand's own typer context; its parent ran `basket_callback`.

    Returns:
        The shared manifest and database.

    Raises:
        typer.Exit: Code 2, through `load_manifest_or_exit`, when the manifest is missing or
            fails validation.
    """
    options: _Options = ctx.obj
    loaded = load_manifest_or_exit(options.manifest)
    return BasketCliContext(manifest=loaded, db=loaded.resolve_path(loaded.hive.db))


def open_store(cli_ctx: BasketCliContext) -> SqliteBasketStore:
    """Open the Hive's Basket store: metadata in its database, bytes where `[basket] root` says.

    Must run outside any event loop: `open_basket_store` runs its own `asyncio.run`.

    Args:
        cli_ctx: The shared context.

    Returns:
        A ready store capped at `[basket] max_blob_bytes`.
    """
    manifest = cli_ctx.manifest
    # The same rule `open_default_stores` applies, so this command and the running Hive read and
    # write one byte tree: an explicit root resolves like every manifest path, else `basket/`
    # beside the database.
    root = manifest.basket.root
    resolved = manifest.resolve_path(root) if root is not None else cli_ctx.db.parent / "basket"
    return open_basket_store(cli_ctx.db, resolved, manifest.basket.max_blob_bytes)


def open_lab(cli_ctx: BasketCliContext) -> OpenedLab:
    """Build the Hive's deposit door with its Honey port, exactly as the running Hive does.

    Must run outside any event loop: every `open_*` here runs its own `asyncio.run`.

    Args:
        cli_ctx: The shared context.

    Returns:
        The RoyalJellyAccess `build_royal_jelly` built, the trail its events go to, and the model
        registry the caller closes after its run.
    """
    manifest = cli_ctx.manifest
    stores = open_default_stores(manifest)
    # The Honey Store's handles, so each new blob's Honey record lands in the real store; the
    # access's registry is the one `build_royal_jelly` resolves the SCAFFOLDER slot against.
    honey = open_access(HoneyCliContext(manifest, cli_ctx.db, _WIDEST))
    access = build_royal_jelly(manifest, stores, honey.registry, honey.access, SystemClock())
    # Only a hand-built HiveStores lacks the registry or the Basket, and open_default_stores has
    # both; say so rather than carry on with no deposit door.
    if access is None:
        typer.echo("the Hive's stores hold no Basket or Comb Registry", err=True)
        raise typer.Exit(code=EXIT_REFUSED)
    return OpenedLab(access=access, trail=stores.trail, registry=honey.registry)


async def resolve_sha256(store: BasketStore, text: str) -> str:
    """Resolve a full sha256 or an unambiguous prefix of one to the blob's full hash.

    Args:
        store: The Basket store to look in.
        text: What the operator typed: 64 hex digits, or the front of them.

    Returns:
        The full hash of the one blob it names.

    Raises:
        BlobNotFoundError: No blob has this hash or this prefix.
        ValueError: More than one blob begins with this prefix.
    """
    candidate = text.strip().lower()
    # A full digest is looked up by the store itself, which also refuses a non-digest.
    if len(candidate) == SHA256_CHARS:
        return candidate
    matches = [
        blob.sha256 for blob in await store.list_blobs() if blob.sha256.startswith(candidate)
    ]
    # An empty prefix would match everything; it names nothing.
    if not candidate or not matches:
        raise BlobNotFoundError(text)
    if len(matches) > 1:
        raise ValueError(f"{text!r} matches {len(matches)} blobs; give more digits.")
    return matches[0]
