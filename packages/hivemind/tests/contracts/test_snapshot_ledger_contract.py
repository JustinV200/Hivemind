"""Contract tests: hivemind.hive.snapshot.ledger.SnapshotLedgerPort, both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Runs the same assertions against
    `hivemind.hive.snapshot.SnapshotLedger` (in-memory) and `hivemind.hive.snapshot.
    SqliteSnapshotLedger` (durable), parametrised, so the two stay interchangeable wherever
    `hivemind.hive.snapshot.snapshotter_for`/`hivemind.queen.cell_gate.snapshot.
    CellSnapshotHandler` take a `SnapshotLedgerPort`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.snapshot.ledger for SnapshotLedgerPort and SnapshotLedger.
    - hivemind.hive.snapshot.sqlite_ledger for SqliteSnapshotLedger.
    - packages/hivemind/tests/unit/hive/snapshot/test_ledger.py for the in-memory ledger's own,
      more exhaustive unit tests; this module checks the same contract on both implementations.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import timedelta

import pytest

from hivemind.cell import SnapshotId
from hivemind.common.sqlite import connect
from hivemind.hive.snapshot.ledger import SnapshotLedger, SnapshotLedgerPort, SnapshotNotFoundError
from hivemind.hive.snapshot.ledger import SnapshotRecord as Record
from hivemind.hive.snapshot.sqlite_ledger import SqliteSnapshotLedger
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id

_next_id = itertools.count()


def _build_sqlite_ledger() -> SnapshotLedgerPort:
    return SqliteSnapshotLedger.create(connect(":memory:"), FakeClock())


LEDGER_FACTORIES: tuple[Callable[[], SnapshotLedgerPort], ...] = (
    SnapshotLedger,
    _build_sqlite_ledger,
)


@pytest.fixture(params=LEDGER_FACTORIES, ids=["SnapshotLedger", "SqliteSnapshotLedger"])
def ledger(request: pytest.FixtureRequest) -> SnapshotLedgerPort:
    factory: Callable[[], SnapshotLedgerPort] = request.param
    return factory()


def _record(cell_id: CellId, clock: FakeClock, *, bytes_estimate: int = 100) -> Record:
    taken_at = clock.now()
    return Record(
        id=SnapshotId(f"snap_{next(_next_id)}"),
        cell_id=cell_id,
        taken_at=taken_at,
        bytes_estimate=bytes_estimate,
        expires_at=taken_at + timedelta(seconds=3600.0),
    )


def test_record_then_get_returns_the_same_record(ledger: SnapshotLedgerPort) -> None:
    clock = FakeClock()
    record = _record(new_cell_id(clock), clock)

    ledger.record(record)

    assert ledger.get(record.id) == record


def test_get_raises_for_an_unknown_id(ledger: SnapshotLedgerPort) -> None:
    with pytest.raises(SnapshotNotFoundError):
        ledger.get(SnapshotId("snap_does_not_exist"))


def test_disk_used_bytes_sums_only_that_cells_own_records(ledger: SnapshotLedgerPort) -> None:
    clock = FakeClock()
    cell_a, cell_b = new_cell_id(clock), new_cell_id(clock)
    ledger.record(_record(cell_a, clock, bytes_estimate=100))
    ledger.record(_record(cell_a, clock, bytes_estimate=50))
    ledger.record(_record(cell_b, clock, bytes_estimate=999))

    assert ledger.disk_used_bytes(cell_a) == 150
    assert ledger.disk_used_bytes(cell_b) == 999
    assert ledger.disk_used_bytes(new_cell_id(clock)) == 0


def test_oldest_for_cell_returns_the_earliest_taken_at(ledger: SnapshotLedgerPort) -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    first = _record(cell_id, clock)
    ledger.record(first)
    clock.advance(10.0)
    ledger.record(_record(cell_id, clock))

    assert ledger.oldest_for_cell(cell_id) == first.id
    assert ledger.oldest_for_cell(new_cell_id(clock)) is None


def test_room_for_evicts_the_oldest_only_when_over_budget(ledger: SnapshotLedgerPort) -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    assert ledger.room_for(cell_id, 10_000_000, None) is None

    oldest = _record(cell_id, clock, bytes_estimate=800)
    ledger.record(oldest)
    clock.advance(1.0)
    ledger.record(_record(cell_id, clock, bytes_estimate=100))

    assert ledger.room_for(cell_id, 50, budget_bytes=1000) is None
    assert ledger.room_for(cell_id, 200, budget_bytes=1000) == oldest.id


def test_expire_removes_and_returns_only_past_deadline_records(ledger: SnapshotLedgerPort) -> None:
    clock = FakeClock()
    cell_id = new_cell_id(clock)
    expiring_soon = Record(
        id=SnapshotId(f"snap_{next(_next_id)}"),
        cell_id=cell_id,
        taken_at=clock.now(),
        bytes_estimate=1,
        expires_at=clock.now() + timedelta(seconds=10.0),
    )
    lasts_longer = _record(cell_id, clock)
    ledger.record(expiring_soon)
    ledger.record(lasts_longer)
    clock.advance(20.0)

    expired = ledger.expire(clock.now())

    assert expired == (expiring_soon.id,)
    assert ledger.get(lasts_longer.id) == lasts_longer
    with pytest.raises(SnapshotNotFoundError):
        ledger.get(expiring_soon.id)


def test_delete_removes_a_known_record_and_raises_for_an_unknown_one(
    ledger: SnapshotLedgerPort,
) -> None:
    clock = FakeClock()
    record = _record(new_cell_id(clock), clock)
    ledger.record(record)

    ledger.delete(record.id)

    with pytest.raises(SnapshotNotFoundError):
        ledger.get(record.id)
    with pytest.raises(SnapshotNotFoundError):
        ledger.delete(SnapshotId("snap_does_not_exist"))


def test_delete_for_cell_removes_every_record_for_that_cell_only(
    ledger: SnapshotLedgerPort,
) -> None:
    clock = FakeClock()
    cell_a, cell_b = new_cell_id(clock), new_cell_id(clock)
    a1, a2 = _record(cell_a, clock), _record(cell_a, clock, bytes_estimate=1)
    b1 = _record(cell_b, clock)
    ledger.record(a1)
    ledger.record(a2)
    ledger.record(b1)

    removed = ledger.delete_for_cell(cell_a)

    assert set(removed) == {a1.id, a2.id}
    assert ledger.disk_used_bytes(cell_a) == 0
    assert ledger.get(b1.id) == b1
    assert ledger.delete_for_cell(new_cell_id(clock)) == ()
