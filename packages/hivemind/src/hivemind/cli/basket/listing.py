"""Provide `hive basket list` and `hive basket show`: read the Basket, change nothing.

The Basket (ADR-0048) keeps inert blobs by content hash; a blob grants nothing to whoever holds
it. `list [--kind K] [--state present|retired] [--cell ID] [--json]` prints one row per blob,
oldest deposit first: short sha256, size, kind, media type, platform tags, clearance label,
origin Cell, when it was deposited, and whether it is tainted (never shipped or loaded).
`show SHA [--json]` prints one blob in full: its facts, who signed its manifest and whether the
signature verifies against the Hive key, which Cells hold it resident, and whether the Honey Store
holds its record (the `source_key` `basket:<sha256>` finds it with `hive honey`). SHA is the full
hash or any unambiguous prefix. A signature that does not verify exits 1, still after printing.

Residency is read per Cell, so `show` asks the Cells it can name: the Hive Stand, the blob's
origin Cell and every destination of a pending shipment of it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `basket` command group. Mounted by
    `hivemind.cli.basket` as `list` and `show`. Calls into `hivemind.basket`, `hivemind.cell`,
    `hivemind.cli.stores`, `hivemind.cli.compose.royal_jelly` (the Hive key's verifier), and this
    group's own `context` and `render` only.

Key invariants:
    - Read-only: nothing here writes the Basket or the trail, and no blob's bytes are read or run.
    - A blob with no such hash exits 1 with the Basket's code.

See Also:
    - hivemind.basket.manifest for BlobManifest.verify, the signature check.
    - hivemind.cli.basket.lifecycle for the commands that change a blob's standing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated

import typer

from hivemind.basket import (
    BasketError,
    BasketStore,
    Blob,
    BlobManifest,
    BlobState,
    MediaKind,
    ResidentBlob,
)
from hivemind.cell import hive_stand_cell_id
from hivemind.cli.basket.context import BasketCliContext, cli_context, open_store, resolve_sha256
from hivemind.cli.basket.render import (
    SOURCE_KEY_PREFIX,
    BlobDetail,
    blob_detail_json,
    blobs_json,
    print_blob_detail,
    print_blobs,
)
from hivemind.cli.stores import JsonOption, open_honey_store
from hivemind.cli.tools.context import EXIT_REFUSED, cell_id_of, run_or_exit
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.honey_store import HoneyStore
from waggle.ids import CellId
from waggle.signing import Ed25519Verifier

__all__ = ["list_command", "show_command"]

ShaArgument = Annotated[str, typer.Argument(help="A blob's sha256, or the front of it.")]
KindOption = Annotated[
    MediaKind | None, typer.Option("--kind", case_sensitive=False, help="Only this kind.")
]
StateOption = Annotated[
    BlobState | None,
    typer.Option("--state", case_sensitive=False, help="Only blobs present or retired."),
]
OriginOption = Annotated[
    str | None, typer.Option("--cell", help="Only blobs deposited from this Cell.")
]


def list_command(
    ctx: typer.Context,
    kind: KindOption = None,
    state: StateOption = None,
    cell: OriginOption = None,
    as_json: JsonOption = False,
) -> None:
    """List the Basket's blobs, oldest deposit first, optionally narrowed."""
    store = open_store(cli_context(ctx))
    # Local SQLite, one indexed read: filtered by kind and state in the store, by origin Cell too.
    blobs = run_or_exit(store.list_blobs(kind, state, cell_id_of(cell)))
    if as_json:
        typer.echo(blobs_json(blobs))
        return
    print_blobs(blobs)


def show_command(ctx: typer.Context, sha256: ShaArgument, as_json: JsonOption = False) -> None:
    """Show one blob in full: signer and signature check, residency, and its Honey record's key."""
    cli_ctx = cli_context(ctx)
    lookup = _Lookup(
        store=open_store(cli_ctx),
        honey=open_honey_store(cli_ctx.db),
        verifier=_hive_verifier(cli_ctx),
        hive_stand=hive_stand_cell_id(cli_ctx.manifest.hive.node_id),
    )
    detail = run_or_exit(_detail(lookup, sha256))
    if as_json:
        typer.echo(blob_detail_json(detail))
    else:
        print_blob_detail(detail)
    # A manifest that does not verify was altered, or signed by a key this Hive does not trust:
    # the blob is shown so the operator can look, but the command fails so a script notices.
    if detail.signature_error is not None:
        raise typer.Exit(code=EXIT_REFUSED)


@dataclass(frozen=True, slots=True)
class _Lookup:
    """What `show` reads from: the Basket, the Honey Store, the Hive key and the Hive Stand."""

    store: BasketStore  # Where the blob, its manifest and its residency live.
    honey: HoneyStore  # Where the blob's record is looked up by its source key.
    verifier: Ed25519Verifier  # Trusts exactly the Hive's own key.
    hive_stand: CellId  # The Hive Stand's Cell, always a residency candidate.


def _hive_verifier(cli_ctx: BasketCliContext) -> Ed25519Verifier:
    """Return a verifier that trusts exactly the Hive's own key, as `RoyalJellyAccess.verifier`."""
    manifest = cli_ctx.manifest
    secrets = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    # SAFETY: a fresh event loop for this one small file read (the Hive minted the key at its first
    # run; a Hive that never ran has signed nothing, and its blobs then fail as unknown signer).
    signer = asyncio.run(load_or_mint_hive_signer(secrets))
    return Ed25519Verifier({manifest.hive.node_id: signer.public_key_bytes})


async def _detail(lookup: _Lookup, text: str) -> BlobDetail:
    """Gather everything `show` prints about one blob."""
    sha256 = await resolve_sha256(lookup.store, text)
    blob = await lookup.store.get(sha256)
    manifest = await lookup.store.manifest(sha256)
    return BlobDetail(
        blob=blob,
        signer=manifest.signer,
        signature_error=_signature_error(manifest, lookup.verifier),
        residency=await _residency(lookup.store, blob, lookup.hive_stand),
        honey_recorded=await lookup.honey.has_source(f"{SOURCE_KEY_PREFIX}{sha256}"),
    )


def _signature_error(manifest: BlobManifest, verifier: Ed25519Verifier) -> str | None:
    """Check a manifest's signature; return None when it verifies, else the Basket's reason."""
    try:
        manifest.verify(verifier)
    except BasketError as exc:
        return f"{exc.code}: {exc}"
    return None


async def _residency(
    store: BasketStore, blob: Blob, hive_stand: CellId
) -> tuple[tuple[str, ResidentBlob], ...]:
    """Return each Cell known to hold the blob resident, with its row."""
    # The store lists residency per Cell, so ask the Cells that can be named: the Hive Stand, the
    # origin, and anywhere a pending shipment is taking it.
    pending = await store.pending_shipments()
    destinations = {s.destination_cell_id for s in pending if s.sha256 == blob.sha256}
    found: list[tuple[str, ResidentBlob]] = []
    for cell_id in sorted({hive_stand, blob.origin_cell_id, *destinations}):
        rows = await store.resident(cell_id)
        found.extend((cell_id, row) for row in rows if row.sha256 == blob.sha256)
    return tuple(found)
