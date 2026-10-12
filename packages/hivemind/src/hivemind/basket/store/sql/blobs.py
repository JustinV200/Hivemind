"""Read and write the `basket_blobs` table: the blob rows, their manifests and their state.

Plain synchronous functions over one open connection, run on the store's `ConnectionThread`; the
store decides what to lock and when to hop threads. This module owns the translation between a row
and the `Blob`/`BlobManifest` models, the dedupe rule (a repeat deposit returns the existing row
and may only raise its label) and `retire`. It never touches the byte directory except through the
`BlobFiles` it is handed.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store.sql`.
    Called by `SqliteBasketStore` only. Calls into `hivemind.basket.models`, `manifest`, `errors`
    and `hivemind.common.sqlite` (transaction).

Key invariants:
    - Every write that touches more than one statement runs inside one `transaction`.
    - A dedupe never lowers a label and never rewrites an existing, still-verifying file.
    - `blob_from_row` is the only place a row becomes a `Blob`, so a column added later has one
      place to be read.

See Also:
    - hivemind.basket.store.migrations for the table these functions read and write.
    - hivemind.basket.store.sqlite for the caller.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime

from hivemind.basket.errors import BlobIntegrityError, BlobNotFoundError
from hivemind.basket.manifest import BlobManifest
from hivemind.basket.models import Blob, BlobState, MediaKind
from hivemind.basket.store.files import BlobFiles
from hivemind.cell import HoneyClearance, OsFamily
from hivemind.common.sqlite import transaction
from waggle.ids import CellId

__all__ = [
    "blob_from_row",
    "deposit_blob",
    "list_rows",
    "manifest_from_row",
    "require_row",
    "retire_blob",
]

_SELECT_ONE = "SELECT * FROM basket_blobs WHERE sha256 = ?"
_INSERT = """
INSERT INTO basket_blobs (
    sha256, size_bytes, media_kind, media_type, platform_tags, clearance, clearance_rank,
    origin_cell_id, origin_task_id, origin_bee, deposited_at, deposit_event_id, tainted, state,
    manifest_signer, manifest_signature
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_RAISE_LABEL = """
UPDATE basket_blobs SET clearance = ?, clearance_rank = ?, tainted = ?,
    manifest_signer = ?, manifest_signature = ? WHERE sha256 = ?
"""
_RETIRE = (
    "UPDATE basket_blobs SET state = 'RETIRED', retired_at = ?, retired_reason = ? WHERE sha256 = ?"
)


def blob_from_row(row: sqlite3.Row) -> Blob:
    """Decode one `basket_blobs` row into a Blob."""
    return Blob(
        sha256=row["sha256"],
        size_bytes=row["size_bytes"],
        media_kind=MediaKind(row["media_kind"]),
        media_type=row["media_type"],
        platform_tags=tuple(OsFamily(tag) for tag in json.loads(row["platform_tags"])),
        clearance=HoneyClearance(row["clearance"]),
        origin_cell_id=row["origin_cell_id"],
        origin_task_id=row["origin_task_id"],
        origin_bee=row["origin_bee"],
        deposited_at=datetime.fromisoformat(row["deposited_at"]),
        deposit_event_id=row["deposit_event_id"],
        tainted=bool(row["tainted"]),
        state=BlobState(row["state"]),
    )


def manifest_from_row(row: sqlite3.Row) -> BlobManifest:
    """Decode one row's blob plus its stored signer and signature into a BlobManifest."""
    return BlobManifest(
        blob=blob_from_row(row),
        signer=row["manifest_signer"],
        signature=row["manifest_signature"],
    )


def require_row(connection: sqlite3.Connection, sha256: str) -> sqlite3.Row:
    """Return the row for `sha256`, or raise BlobNotFoundError."""
    row = connection.execute(_SELECT_ONE, (sha256,)).fetchone()
    if row is None:
        raise BlobNotFoundError(sha256)
    found: sqlite3.Row = row
    return found


def list_rows(
    connection: sqlite3.Connection,
    media_kind: MediaKind | None,
    state: BlobState | None,
    origin_cell_id: CellId | None,
) -> tuple[Blob, ...]:
    """List blobs matching every filter given, oldest deposit first."""
    # Only the filters the caller gave become clauses; values are bound, never formatted in.
    clauses: list[str] = []
    values: list[str] = []
    for column, value in (
        ("media_kind", media_kind.value if media_kind else None),
        ("state", state.value if state else None),
        ("origin_cell_id", origin_cell_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            values.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(f"SELECT * FROM basket_blobs{where}", values).fetchall()  # noqa: S608
    # Sorted on the parsed time, not the stored text, so ordering never depends on ISO formatting.
    blobs = [blob_from_row(row) for row in rows]
    return tuple(sorted(blobs, key=lambda blob: (blob.deposited_at, blob.sha256)))


def deposit_blob(
    connection: sqlite3.Connection,
    files: BlobFiles,
    data: bytes,
    blob: Blob,
    manifest: BlobManifest,
) -> tuple[Blob, bool]:
    """Store a blob and its bytes, or dedupe onto the existing row; see `BasketStore.deposit`."""
    _check_bytes(data, blob, manifest)
    existing_row = connection.execute(_SELECT_ONE, (blob.sha256,)).fetchone()
    if existing_row is not None:
        # Heal an orphaned row (its file lost) without ever rewriting a file that is there.
        if not files.exists(blob.sha256):
            files.write(blob.sha256, data)
        return _dedupe(connection, blob, manifest, blob_from_row(existing_row)), False
    # New: the bytes first (atomic rename), then the row. A crash between leaves an orphan file,
    # which the next deposit of the same bytes adopts, never a row pointing at nothing.
    files.write(blob.sha256, data)
    with transaction(connection):
        connection.execute(_INSERT, _insert_values(blob, manifest))
    return blob, True


def retire_blob(connection: sqlite3.Connection, sha256: str, reason: str, now: datetime) -> Blob:
    """Mark a blob RETIRED (keeping row and bytes) and return it; idempotent."""
    row = require_row(connection, sha256)
    # Already retired: nothing to change, and the original reason and time are kept.
    if row["state"] == BlobState.RETIRED.value:
        return blob_from_row(row)
    with transaction(connection):
        connection.execute(_RETIRE, (now.isoformat(), reason, sha256))
    return blob_from_row(require_row(connection, sha256))


def _check_bytes(data: bytes, blob: Blob, manifest: BlobManifest) -> None:
    """Refuse bytes that are not the blob's, or a manifest that describes another blob."""
    # The store trusts nobody upstream: a mismatch here would key wrong bytes under a right name.
    if len(data) != blob.size_bytes:
        raise BlobIntegrityError(
            blob.sha256, f"{len(data)} bytes offered, {blob.size_bytes} declared"
        )
    if hashlib.sha256(data).hexdigest() != blob.sha256:
        raise BlobIntegrityError(
            blob.sha256, "the offered bytes do not hash to the declared sha256"
        )
    if manifest.blob.sha256 != blob.sha256:
        raise BlobIntegrityError(blob.sha256, "the manifest describes a different blob")


def _dedupe(
    connection: sqlite3.Connection, blob: Blob, manifest: BlobManifest, existing: Blob
) -> Blob:
    """Return the existing blob, raising its label if the repeat deposit's is higher."""
    higher = blob.clearance.rank > existing.clearance.rank
    newly_tainted = blob.tainted and not existing.tainted
    if not (higher or newly_tainted):
        return existing
    # Raise only: same bytes arriving from a more sensitive place must never be handled as less.
    merged = existing.model_copy(
        update={
            "clearance": blob.clearance if higher else existing.clearance,
            "tainted": existing.tainted or blob.tainted,
        }
    )
    if manifest.blob != merged:
        raise BlobIntegrityError(blob.sha256, "the manifest does not describe the merged label")
    with transaction(connection):
        connection.execute(
            _RAISE_LABEL,
            (
                merged.clearance.value,
                merged.clearance.rank,
                int(merged.tainted),
                manifest.signer,
                manifest.signature,
                blob.sha256,
            ),
        )
    return merged


def _insert_values(blob: Blob, manifest: BlobManifest) -> tuple[object, ...]:
    """Flatten a blob and its manifest into the `_INSERT` parameter order."""
    return (
        blob.sha256,
        blob.size_bytes,
        blob.media_kind.value,
        blob.media_type,
        json.dumps([tag.value for tag in blob.platform_tags]),
        blob.clearance.value,
        blob.clearance.rank,
        blob.origin_cell_id,
        blob.origin_task_id,
        blob.origin_bee,
        blob.deposited_at.isoformat(),
        blob.deposit_event_id,
        int(blob.tainted),
        blob.state.value,
        manifest.signer,
        manifest.signature,
    )
