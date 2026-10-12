"""Read and write the `basket_shipments` table: blobs queued to ship to a Cell.

Plain synchronous functions over one open connection, run on the store's `ConnectionThread`. A
shipment is a durable request ("get this blob to that Cell, for this reason"); the Queen's Basket
tick works the queue and calls `complete_shipment` when the transfer finishes, so a restart never
forgets a shipment that was queued but not delivered.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store.sql`.
    Called by `SqliteBasketStore` only. Calls into `hivemind.basket.store.rows`, `errors` and
    `hivemind.common.sqlite` (transaction).

Key invariants:
    - A shipment is pending exactly while `completed_at` is NULL; completing twice keeps the first
      completion time.
    - A shipment can only name a blob the Basket holds.

See Also:
    - hivemind.basket.transfer for the state machines that move the bytes once a shipment is due.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.basket.errors import BlobNotFoundError, ShipmentNotFoundError
from hivemind.basket.store.rows import Shipment
from hivemind.common.sqlite import transaction
from waggle.ids import CellId

__all__ = ["complete_shipment", "list_pending", "queue_shipment"]

_COLUMNS = "id, sha256, destination_cell_id, reason, queued_at, completed_at"


def queue_shipment(
    connection: sqlite3.Connection,
    sha256: str,
    destination_cell_id: CellId,
    reason: str,
    now: datetime,
) -> Shipment:
    """Queue a shipment of a known blob and return it."""
    known = connection.execute("SELECT 1 FROM basket_blobs WHERE sha256 = ?", (sha256,)).fetchone()
    if known is None:
        raise BlobNotFoundError(sha256)
    with transaction(connection):
        cursor = connection.execute(
            "INSERT INTO basket_shipments (sha256, destination_cell_id, reason, queued_at) "
            "VALUES (?, ?, ?, ?)",
            (sha256, destination_cell_id, reason, now.isoformat()),
        )
    return Shipment(
        id=int(cursor.lastrowid or 0),
        sha256=sha256,
        destination_cell_id=destination_cell_id,
        reason=reason,
        queued_at=now,
        completed_at=None,
    )


def list_pending(connection: sqlite3.Connection) -> tuple[Shipment, ...]:
    """Return every shipment not yet completed, in the order they were queued."""
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM basket_shipments WHERE completed_at IS NULL ORDER BY id"  # noqa: S608
    ).fetchall()
    return tuple(_shipment_from_row(row) for row in rows)


def complete_shipment(connection: sqlite3.Connection, shipment_id: int, now: datetime) -> None:
    """Mark a shipment completed; a repeat is a no-op, an unknown id is an error."""
    with transaction(connection):
        # Only a still-pending row changes, so the first completion time survives a repeat.
        cursor = connection.execute(
            "UPDATE basket_shipments SET completed_at = ? WHERE id = ? AND completed_at IS NULL",
            (now.isoformat(), shipment_id),
        )
        if cursor.rowcount == 1:
            return
        exists = connection.execute(
            "SELECT 1 FROM basket_shipments WHERE id = ?", (shipment_id,)
        ).fetchone()
        if exists is None:
            raise ShipmentNotFoundError(shipment_id)


def _shipment_from_row(row: sqlite3.Row) -> Shipment:
    """Decode one `basket_shipments` row into a Shipment."""
    done = row["completed_at"]
    return Shipment(
        id=row["id"],
        sha256=row["sha256"],
        destination_cell_id=row["destination_cell_id"],
        reason=row["reason"],
        queued_at=datetime.fromisoformat(row["queued_at"]),
        completed_at=datetime.fromisoformat(done) if done else None,
    )
