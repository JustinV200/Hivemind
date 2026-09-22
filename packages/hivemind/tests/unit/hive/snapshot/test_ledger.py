"""Unit tests for hivemind.hive.snapshot.ledger: SnapshotRecord/SnapshotLedger accounting.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/snapshot/ledger.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.snapshot.ledger for SnapshotLedger, the class under test.
"""

from __future__ import annotations

import itertools
from datetime import timedelta

import pytest

from hivemind.cell import SnapshotId
from hivemind.hive.snapshot.ledger import SnapshotLedger, SnapshotNotFoundError, SnapshotRecord
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id

# A FakeClock does not tick between calls unless a test explicitly advances it, so two records
# built back to back can share the same taken_at; this counter is what keeps every _record call's
# own id unique regardless, mirroring hive.snapshot.docker/.qemu's own _next_seq field.
_next_id = itertools.count()


def _record(
    cell_id: CellId,
    clock: FakeClock,
    *,
    bytes_estimate: int = 100,
    retention_s: float = 3600.0,
) -> SnapshotRecord:
    taken_at = clock.now()
    return SnapshotRecord(
        id=SnapshotId(f"snap_{next(_next_id)}"),
        cell_id=cell_id,
        taken_at=taken_at,
        bytes_estimate=bytes_estimate,
        expires_at=taken_at + timedelta(seconds=retention_s),
    )


async def test_record_then_get_returns_the_same_record() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    cell_id = new_cell_id(clock)
    record = _record(cell_id, clock)

    ledger.record(record)

    assert ledger.get(record.id) == record


async def test_get_raises_for_an_unknown_id() -> None:
    ledger = SnapshotLedger()

    with pytest.raises(SnapshotNotFoundError):
        ledger.get(SnapshotId("snap_does_not_exist"))


async def test_disk_used_bytes_sums_only_that_cells_own_records() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    cell_a = new_cell_id(clock)
    cell_b = new_cell_id(clock)
    ledger.record(_record(cell_a, clock, bytes_estimate=100))
    ledger.record(_record(cell_a, clock, bytes_estimate=50))
    ledger.record(_record(cell_b, clock, bytes_estimate=999))

    assert ledger.disk_used_bytes(cell_a) == 150
    assert ledger.disk_used_bytes(cell_b) == 999


async def test_disk_used_bytes_is_zero_for_a_cell_with_no_snapshots() -> None:
    ledger = SnapshotLedger()

    assert ledger.disk_used_bytes(new_cell_id(FakeClock())) == 0


async def test_oldest_for_cell_returns_the_earliest_taken_at() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    cell_id = new_cell_id(clock)
    first = _record(cell_id, clock)
    ledger.record(first)
    clock.advance(10.0)
    ledger.record(_record(cell_id, clock))

    assert ledger.oldest_for_cell(cell_id) == first.id


async def test_oldest_for_cell_returns_none_when_empty() -> None:
    ledger = SnapshotLedger()

    assert ledger.oldest_for_cell(new_cell_id(FakeClock())) is None


async def test_room_for_returns_none_with_no_budget_configured() -> None:
    ledger = SnapshotLedger()
    cell_id = new_cell_id(FakeClock())

    assert ledger.room_for(cell_id, 10_000_000, None) is None


async def test_room_for_returns_none_when_incoming_bytes_still_fit() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    cell_id = new_cell_id(clock)
    ledger.record(_record(cell_id, clock, bytes_estimate=100))

    assert ledger.room_for(cell_id, 50, budget_bytes=1000) is None


async def test_room_for_returns_the_oldest_id_when_over_budget() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    cell_id = new_cell_id(clock)
    oldest = _record(cell_id, clock, bytes_estimate=800)
    ledger.record(oldest)
    clock.advance(1.0)
    ledger.record(_record(cell_id, clock, bytes_estimate=100))

    assert ledger.room_for(cell_id, 200, budget_bytes=1000) == oldest.id


async def test_expire_removes_and_returns_only_past_deadline_records() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    cell_id = new_cell_id(clock)
    expiring_soon = _record(cell_id, clock, retention_s=10.0)
    lasts_longer = _record(cell_id, clock, retention_s=1000.0)
    ledger.record(expiring_soon)
    ledger.record(lasts_longer)
    clock.advance(20.0)

    expired = ledger.expire(clock.now())

    assert expired == (expiring_soon.id,)
    assert ledger.get(lasts_longer.id) == lasts_longer
    with pytest.raises(SnapshotNotFoundError):
        ledger.get(expiring_soon.id)


async def test_delete_removes_a_known_record() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    record = _record(new_cell_id(clock), clock)
    ledger.record(record)

    ledger.delete(record.id)

    with pytest.raises(SnapshotNotFoundError):
        ledger.get(record.id)


async def test_delete_raises_for_an_unknown_id() -> None:
    ledger = SnapshotLedger()

    with pytest.raises(SnapshotNotFoundError):
        ledger.delete(SnapshotId("snap_does_not_exist"))


async def test_delete_for_cell_removes_every_record_for_that_cell_only() -> None:
    clock = FakeClock()
    ledger = SnapshotLedger()
    cell_a = new_cell_id(clock)
    cell_b = new_cell_id(clock)
    a1 = _record(cell_a, clock)
    a2 = _record(cell_a, clock, bytes_estimate=1)
    b1 = _record(cell_b, clock)
    ledger.record(a1)
    ledger.record(a2)
    ledger.record(b1)

    removed = ledger.delete_for_cell(cell_a)

    assert set(removed) == {a1.id, a2.id}
    assert ledger.disk_used_bytes(cell_a) == 0
    assert ledger.get(b1.id) == b1


async def test_delete_for_cell_is_idempotent_for_a_cell_with_no_snapshots() -> None:
    ledger = SnapshotLedger()

    assert ledger.delete_for_cell(new_cell_id(FakeClock())) == ()
