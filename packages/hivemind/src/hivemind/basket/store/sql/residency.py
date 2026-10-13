"""Read and write the `basket_residency` table: which blobs each Cell keeps on its own disk.

Plain synchronous functions over one open connection, run on the store's `ConnectionThread`. A
residency row says "this Cell holds this blob", with when it arrived and when it was last used;
`hivemind.basket.residency` reads them to decide what to evict when a new blob must fit under the
Cell's ceiling. A row can only exist for a blob the Basket knows (a foreign key plus an explicit
check, so the error is `BlobNotFoundError`, not an SQLite constraint message).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store.sql`.
    Called by `SqliteBasketStore` only. Calls into `hivemind.basket.store.rows`, `errors` and
    `hivemind.common.sqlite` (transaction).

Key invariants:
    - `mark_resident` is an upsert that never moves `resident_since` back or forward once set:
      only `last_used` changes on a repeat.
    - `resident_bytes` is the sum of the blobs' own sizes, so it can never disagree with them.

See Also:
    - hivemind.basket.residency for the pure eviction decision over these rows.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.basket.errors import BlobNotFoundError
from hivemind.basket.store.rows import ResidentBlob
from hivemind.common.sqlite import transaction
from waggle.ids import CellId

__all__ = ["evict_resident", "list_resident", "mark_resident", "sum_resident_bytes"]

_UPSERT = """
INSERT INTO basket_residency (cell_id, sha256, resident_since, last_used) VALUES (?, ?, ?, ?)
ON CONFLICT (cell_id, sha256) DO UPDATE SET last_used = excluded.last_used
"""
_LIST = """
SELECT r.sha256, b.size_bytes, r.resident_since, r.last_used
FROM basket_residency r JOIN basket_blobs b ON b.sha256 = r.sha256 WHERE r.cell_id = ?
"""
_SUM = """
SELECT COALESCE(SUM(b.size_bytes), 0) AS total
FROM basket_residency r JOIN basket_blobs b ON b.sha256 = r.sha256 WHERE r.cell_id = ?
"""


def list_resident(connection: sqlite3.Connection, cell_id: CellId) -> tuple[ResidentBlob, ...]:
    """Return a Cell's resident blobs, least recently used first."""
    rows = connection.execute(_LIST, (cell_id,)).fetchall()
    resident = [
        ResidentBlob(
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            resident_since=datetime.fromisoformat(row["resident_since"]),
            last_used=datetime.fromisoformat(row["last_used"]),
        )
        for row in rows
    ]
    # Ordered on the parsed times (then hash, for a stable tie-break), the order eviction wants.
    return tuple(
        sorted(resident, key=lambda blob: (blob.last_used, blob.resident_since, blob.sha256))
    )


def mark_resident(
    connection: sqlite3.Connection, cell_id: CellId, sha256: str, since: datetime
) -> None:
    """Record a Cell as holding a blob, or refresh its `last_used` if it already does."""
    # Checked explicitly so a missing blob is a Basket error, not an SQLite constraint message.
    known = connection.execute("SELECT 1 FROM basket_blobs WHERE sha256 = ?", (sha256,)).fetchone()
    if known is None:
        raise BlobNotFoundError(sha256)
    stamp = since.isoformat()
    with transaction(connection):
        connection.execute(_UPSERT, (cell_id, sha256, stamp, stamp))


def evict_resident(connection: sqlite3.Connection, cell_id: CellId, sha256: str) -> None:
    """Forget that a Cell holds a blob; a blob it never held is a quiet no-op."""
    with transaction(connection):
        connection.execute(
            "DELETE FROM basket_residency WHERE cell_id = ? AND sha256 = ?", (cell_id, sha256)
        )


def sum_resident_bytes(connection: sqlite3.Connection, cell_id: CellId) -> int:
    """Return the total size of the blobs a Cell keeps resident."""
    row = connection.execute(_SUM, (cell_id,)).fetchone()
    return int(row["total"])
