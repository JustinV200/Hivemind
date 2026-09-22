"""Define SnapshotRecord and SnapshotLedger: the in-memory book of every live Cell snapshot.

Roadmap step 5.10: "Both record SnapshotRecord(id, cell_id, taken_at, bytes_estimate, expires_at)
in an in-memory SnapshotLedger... with disk_used_bytes(cell_id) for Forage accounting, expire(now)
-> ids and a delete(snapshot_id)." One `SnapshotLedger` is shared by `hivemind.hive.snapshot.docker.
DockerSnapshotter` and `hivemind.hive.snapshot.qemu.QemuSnapshotter` (injected, codingrules section
8.2), so a Cell's disk-Forage accounting and retention/budget bookkeeping stay consistent whichever
backend actually took a given snapshot. Not persisted: a process restart forgets it, the same way
`hivemind.supervision.capping.gate.CappingGate`'s own in-memory proposal table does today
(docs/adr/0018) -- a later phase may give either one a durable store.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.snapshot`. Built once by the composition
    root and handed to every `DockerSnapshotter`/`QemuSnapshotter`; read by
    `hivemind.hive.lifecycle.CellLifecycle.teardown` (deletes a destroyed Cell's own snapshots)
    and, in a later phase, by Forage's own disk accounting. Calls into `hivemind.cell` (SnapshotId)
    and waggle only.

Key invariants:
    - `expire`/`delete`/`delete_for_cell` are the only ways a record ever leaves the ledger;
      `record` never overwrites an id already present (every `SnapshotId` this ledger's callers
      mint is unique per call, codingrules Appendix A.1-style "never silently replace").
    - `get`/`delete` raise `SnapshotNotFoundError` for an id this ledger does not (or no longer)
      hold; every other query method degrades to an empty result instead of raising, since "this
      Cell has no snapshots" is not a failure.
    - `room_for` never deletes anything itself: it only says which id its caller should evict, so
      a Snapshotter can remove the underlying image/VM snapshot first and call `delete` only once
      that succeeds (never leaving the ledger pointing at something already gone from disk).

See Also:
    - .claude/roadmap.md step 5.10 for the SnapshotRecord/SnapshotLedger shape this module builds.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for the in-memory-table precedent
      this ledger follows (the Capping proposal table).
    - hivemind.hive.snapshot.docker and .qemu for this ledger's two writers.
    - hivemind.cell.snapshot for SnapshotId, the id type every record and query here is keyed by.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hivemind.cell import SnapshotId
from waggle.ids import CellId

__all__ = ["SnapshotLedger", "SnapshotLedgerPort", "SnapshotNotFoundError", "SnapshotRecord"]


class SnapshotNotFoundError(Exception):
    """Raised when a snapshot id is not, or is no longer, recorded in a SnapshotLedger.

    Distinct from `hivemind.cell.SnapshotUnsupportedError` (ADR-0018): that one means "this Cell
    has no rollback mechanism at all"; this one means "this Cell has a working Snapshotter, but
    the particular id given was never recorded (or has already expired or been evicted)".
    """

    def __init__(self, snapshot_id: SnapshotId) -> None:
        """Build the error, remembering `snapshot_id` for a caller that wants to inspect it.

        Args:
            snapshot_id: The id no `SnapshotRecord` in the ledger matches.
        """
        super().__init__(f"no snapshot recorded with id {snapshot_id!r}")
        self.snapshot_id = snapshot_id


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    """One live snapshot, as the ledger tracks it: enough to account and expire it.

    Attributes:
        id: This snapshot's own id, as a Snapshotter's `snapshot()` minted it.
        cell_id: The Cell this snapshot was taken of.
        taken_at: When `snapshot()` recorded it.
        bytes_estimate: The snapshot's own estimated disk footprint, for
            `disk_used_bytes`/`room_for` (Forage disk accounting; each Snapshotter's own docstring
            says exactly what this estimates and why it is an estimate, not an exact figure).
        expires_at: When `expire` should remove this record, from the manifest's
            `[virtual_cells] snapshot_retention_s`.
    """

    id: SnapshotId
    cell_id: CellId
    taken_at: datetime
    bytes_estimate: int
    expires_at: datetime


class SnapshotLedgerPort(Protocol):
    """The book of every live snapshot: what `DockerSnapshotter`/`QemuSnapshotter` need from it.

    Roadmap step 5.13's own gap (a durable ledger): `SnapshotLedger` (below) is the in-memory
    implementation used in tests and by a Hive with no database file; `hivemind.hive.snapshot.
    sqlite_ledger.SqliteSnapshotLedger` is the durable one a CLI composition root builds instead,
    so `hive cells rollback` in a separate process can see what an earlier `hive cells snapshot`
    recorded. Every method here is synchronous on both implementations (`SqliteSnapshotLedger`'s
    own module docstring explains why), so `DockerSnapshotter`/`QemuSnapshotter` call either the
    same way, with no `await`.
    """

    def record(self, record: SnapshotRecord) -> None:
        """Add `record` to the ledger, keyed by its own id. See `SnapshotLedger.record`."""
        ...

    def get(self, snapshot_id: SnapshotId) -> SnapshotRecord:
        """Return the record for `snapshot_id`. See `SnapshotLedger.get`."""
        ...

    def disk_used_bytes(self, cell_id: CellId) -> int:
        """Total bytes every live snapshot for `cell_id` accounts for. See `SnapshotLedger`."""
        ...

    def oldest_for_cell(self, cell_id: CellId) -> SnapshotId | None:
        """Oldest live snapshot id for `cell_id`, or None. See `SnapshotLedger.oldest_for_cell`."""
        ...

    def room_for(
        self, cell_id: CellId, incoming_bytes: int, budget_bytes: int | None
    ) -> SnapshotId | None:
        """The snapshot id to evict for `incoming_bytes`, or None. See `SnapshotLedger.room_for`."""
        ...

    def expire(self, now: datetime) -> tuple[SnapshotId, ...]:
        """Remove and return every snapshot expired at or before `now`. See `SnapshotLedger`."""
        ...

    def delete(self, snapshot_id: SnapshotId) -> None:
        """Remove one snapshot's record. See `SnapshotLedger.delete`."""
        ...

    def delete_for_cell(self, cell_id: CellId) -> tuple[SnapshotId, ...]:
        """Remove and return every snapshot recorded for `cell_id`. See `SnapshotLedger`."""
        ...


class SnapshotLedger:
    """The Hive's in-memory book of every live snapshot, across every backend.

    Owns one mutable table (codingrules section 8.5, documented): `_records`, keyed by
    `SnapshotId`. Shared across every `DockerSnapshotter`/`QemuSnapshotter` this Hive builds, so
    a Cell's own accounting is one number regardless of which backend it runs on. Satisfies
    `SnapshotLedgerPort` structurally, with no inheritance needed.
    """

    def __init__(self) -> None:
        """Build a SnapshotLedger holding nothing yet."""
        self._records: dict[SnapshotId, SnapshotRecord] = {}

    def record(self, record: SnapshotRecord) -> None:
        """Add `record` to the ledger, keyed by its own id.

        Args:
            record: A freshly taken snapshot's bookkeeping row.
        """
        self._records[record.id] = record

    def get(self, snapshot_id: SnapshotId) -> SnapshotRecord:
        """Return the record for `snapshot_id`.

        Args:
            snapshot_id: An id a prior `record` call stored.

        Returns:
            The matching SnapshotRecord.

        Raises:
            SnapshotNotFoundError: No record with this id is (or still is) in the ledger.
        """
        try:
            return self._records[snapshot_id]
        except KeyError as exc:
            raise SnapshotNotFoundError(snapshot_id) from exc

    def disk_used_bytes(self, cell_id: CellId) -> int:
        """Return the total bytes every live snapshot for `cell_id` accounts for.

        Args:
            cell_id: The Cell to total.

        Returns:
            The sum of `bytes_estimate` across every record for `cell_id`; 0 if it has none.
        """
        return sum(r.bytes_estimate for r in self._records.values() if r.cell_id == cell_id)

    def oldest_for_cell(self, cell_id: CellId) -> SnapshotId | None:
        """Return the oldest (by `taken_at`) live snapshot id for `cell_id`.

        Args:
            cell_id: The Cell to search.

        Returns:
            The oldest matching snapshot's id, or None if `cell_id` has no live snapshots.
        """
        candidates = [r for r in self._records.values() if r.cell_id == cell_id]
        if not candidates:
            return None
        return min(candidates, key=lambda r: r.taken_at).id

    def room_for(
        self, cell_id: CellId, incoming_bytes: int, budget_bytes: int | None
    ) -> SnapshotId | None:
        """Return the snapshot id to evict before `cell_id` receives one more `incoming_bytes`.

        Manifest `[virtual_cells] snapshot_disk_budget_mb`'s own policy: "over budget, the oldest
        snapshot for that Cell is deleted before a new one is taken." This method only decides;
        the caller (a Snapshotter) deletes the underlying image/VM snapshot first and calls
        `delete` only once that succeeds (module docstring's own key invariant).

        Args:
            cell_id: The Cell about to receive a new snapshot.
            incoming_bytes: The new snapshot's own estimated size.
            budget_bytes: The disk budget configured for one Cell's own snapshots, or None for no
                cap (every existing snapshot stays, however large the total grows).

        Returns:
            `oldest_for_cell(cell_id)` when recording `incoming_bytes` would push
            `disk_used_bytes(cell_id)` past `budget_bytes`; None when there is room, `budget_bytes`
            is None, or `cell_id` has nothing to evict.
        """
        if budget_bytes is None:
            return None
        if self.disk_used_bytes(cell_id) + incoming_bytes <= budget_bytes:
            return None
        return self.oldest_for_cell(cell_id)

    def expire(self, now: datetime) -> tuple[SnapshotId, ...]:
        """Remove and return every snapshot whose `expires_at` is at or before `now`.

        Args:
            now: The reference time; typically the injected Clock's own `.now()`.

        Returns:
            Every expired snapshot's id, in no particular order; empty if nothing has expired.
        """
        expired = tuple(r.id for r in self._records.values() if r.expires_at <= now)
        for snapshot_id in expired:
            del self._records[snapshot_id]
        return expired

    def delete(self, snapshot_id: SnapshotId) -> None:
        """Remove one snapshot's record.

        Args:
            snapshot_id: The id to remove.

        Raises:
            SnapshotNotFoundError: No record with this id is in the ledger.
        """
        if snapshot_id not in self._records:
            raise SnapshotNotFoundError(snapshot_id)
        del self._records[snapshot_id]

    def delete_for_cell(self, cell_id: CellId) -> tuple[SnapshotId, ...]:
        """Remove and return every snapshot recorded for `cell_id`.

        Idempotent, unlike `delete`: a Cell with no snapshots is not an error (`hivemind.hive.
        lifecycle.CellLifecycle.teardown`'s own one-line call reaches this for every torn-down
        Cell, not only ones that were ever snapshotted).

        Args:
            cell_id: The Cell whose snapshots are all going away with it.

        Returns:
            Every removed snapshot's id, in no particular order; empty if it had none.
        """
        ids = tuple(r.id for r in self._records.values() if r.cell_id == cell_id)
        for snapshot_id in ids:
            del self._records[snapshot_id]
        return ids
