"""Print what `hive basket` returns: the blob table, one blob in full, a queued shipment.

Output formatting only (codingrules section 2's CLI row): every value printed here was produced by
`hivemind.basket` -- a `Blob`, its residency rows, a `Shipment` -- and this module decides only how
it looks on a terminal, or as JSON for `--json`. A blob is inert: only its description is printed,
never its bytes, and a blob's Honey text is the Honey Store's to show (the detail view prints the
`source_key` that finds it).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside the CLI's `basket` command group. Called by
    `hivemind.cli.basket.listing`, `.deposit` and `.lifecycle`. Calls into `hivemind.basket` (the
    shapes it prints) only.

Key invariants:
    - Nothing here reads a store or decides anything; it prints what it is given.
    - Every `--json` document is one JSON value on stdout.
    - A hash is shown in short form in a table and in full everywhere a command is meant to be
      copied from.

See Also:
    - hivemind.basket.models for Blob.
    - hivemind.basket.store.rows for ResidentBlob and Shipment.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

import typer

from hivemind.basket import Blob, ResidentBlob, Shipment

SHORT_HASH_CHARS = 12  # Enough of a sha256 to tell blobs apart in a table.
SOURCE_KEY_PREFIX = "basket:"  # The Honey record's `source_key` is `basket:<sha256>`.
_NONE = "-"  # A column with nothing to say.
_HEADERS = (
    "SHA256",
    "SIZE",
    "KIND",
    "MEDIA TYPE",
    "PLATFORMS",
    "CLEARANCE",
    "ORIGIN CELL",
    "DEPOSITED AT",
    "TAINTED",
    "STATE",
)

__all__ = [
    "SHORT_HASH_CHARS",
    "SOURCE_KEY_PREFIX",
    "BlobDetail",
    "blob_detail_json",
    "blobs_json",
    "print_blob_detail",
    "print_blobs",
    "print_shipment",
]


@dataclass(frozen=True, slots=True)
class BlobDetail:
    """Everything `hive basket show` found about one blob."""

    blob: Blob  # The blob as the store holds it.
    signer: str  # The node id that signed its manifest.
    signature_error: str | None  # None when the signature verifies; the reason when it does not.
    residency: tuple[tuple[str, ResidentBlob], ...]  # Each Cell holding it, with its row.
    honey_recorded: bool  # Whether the Honey Store holds the deposit's record.


def print_blobs(blobs: Sequence[Blob]) -> None:
    """Print the blobs as an aligned table, or say there are none.

    Args:
        blobs: The blobs to show, oldest deposit first.
    """
    # An empty Basket: say so rather than print a bare header.
    if not blobs:
        typer.echo("no blobs in the Basket")
        return
    body = [_cells(blob) for blob in blobs]
    widths = [max(len(cell) for cell in column) for column in zip(_HEADERS, *body, strict=True)]
    for cells in (_HEADERS, *body):
        line = "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))
        typer.echo(line.rstrip())


def blobs_json(blobs: Sequence[Blob]) -> str:
    """Return the blobs as one JSON object: each blob's stored fields.

    Args:
        blobs: The blobs to show.

    Returns:
        `{"blobs": [...]}`, one object per blob.
    """
    return json.dumps({"blobs": [blob.model_dump(mode="json") for blob in blobs]}, indent=2)


def print_blob_detail(detail: BlobDetail) -> None:
    """Print one blob in full: its facts, signature, residency and the Honey record's key.

    Args:
        detail: What `show` found.
    """
    for name, value in _facts(detail.blob).items():
        typer.echo(f"{name}: {value}")
    verdict = (
        "verifies against the Hive key"
        if detail.signature_error is None
        else (f"DOES NOT VERIFY ({detail.signature_error})")
    )
    typer.echo(f"signer: {detail.signer}")
    typer.echo(f"signature: {verdict}")
    for cell_id, row in detail.residency:
        typer.echo(
            f"resident: {cell_id} since {row.resident_since.isoformat(timespec='seconds')}, "
            f"last used {row.last_used.isoformat(timespec='seconds')}"
        )
    if not detail.residency:
        typer.echo("resident: nowhere known")
    present = "recorded" if detail.honey_recorded else "NOT FOUND"
    typer.echo(f"honey record: {present} (source_key {SOURCE_KEY_PREFIX}{detail.blob.sha256})")


def blob_detail_json(detail: BlobDetail) -> str:
    """Return one blob's detail as JSON: the blob, its signature verdict and its residency.

    Args:
        detail: What `show` found.

    Returns:
        One JSON object.
    """
    document = {
        "blob": detail.blob.model_dump(mode="json"),
        "signer": detail.signer,
        "signature_verifies": detail.signature_error is None,
        "signature_error": detail.signature_error,
        "resident": [
            {
                "cell_id": cell_id,
                "resident_since": row.resident_since.isoformat(),
                "last_used": row.last_used.isoformat(),
            }
            for cell_id, row in detail.residency
        ],
        "honey_recorded": detail.honey_recorded,
        "source_key": f"{SOURCE_KEY_PREFIX}{detail.blob.sha256}",
    }
    return json.dumps(document, indent=2)


def print_shipment(shipment: Shipment) -> None:
    """Print a queued shipment: its id, blob and destination.

    Args:
        shipment: The queue row `queue_shipment` returned.
    """
    typer.echo(
        f"queued shipment {shipment.id}: {shipment.sha256} to {shipment.destination_cell_id}; "
        "the running Queen's basket tick drains the queue"
    )


def _cells(blob: Blob) -> tuple[str, ...]:
    """Return one blob's table cells, in `_HEADERS` order."""
    return (
        blob.sha256[:SHORT_HASH_CHARS],
        str(blob.size_bytes),
        blob.media_kind.value,
        blob.media_type,
        ",".join(tag.value for tag in blob.platform_tags) or "any",
        blob.clearance.value,
        blob.origin_cell_id,
        blob.deposited_at.isoformat(timespec="seconds"),
        "yes" if blob.tainted else "no",
        blob.state.value,
    )


def _facts(blob: Blob) -> dict[str, str | int | None]:
    """Return a blob's facts as ordered name/value pairs, hash in full."""
    return {
        "sha256": blob.sha256,
        "size": blob.size_bytes,
        "kind": blob.media_kind.value,
        "media type": blob.media_type,
        "platforms": ",".join(tag.value for tag in blob.platform_tags) or "any",
        "clearance": blob.clearance.value,
        "tainted": "yes" if blob.tainted else "no",
        "state": blob.state.value,
        "origin cell": blob.origin_cell_id,
        "origin task": blob.origin_task_id,
        "origin bee": blob.origin_bee,
        "deposited at": blob.deposited_at.isoformat(timespec="seconds"),
    }
