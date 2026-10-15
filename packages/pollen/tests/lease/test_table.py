"""Test the persisted lease table: disk first, then memory, and a restart finds what was there.

The lease table is the persisted record of every open lease on the device (gateway-contract
section 4). Every change is written to disk before it becomes the value the rest of the packet
reads, so a crash leaves the old table or the new one, and a restart reloads exactly what the last
successful write said. Session traffic is admitted only for a lease that is open and whose session
was not closed; anything else is ``hive.session.lease_not_open``. These tests use real files in a
temporary directory.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.lease.table.

Key invariants:
    - A failed write leaves the table, in memory and on disk, as it was.
    - A reload sees what the last successful write saw.

See Also:
    - docs/waggle/gateway-contract.md sections 4 and 12.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from states import sample_state

from pollen.errors import LeaseNotOpenError, StateError
from pollen.lease import DeviceLease, LeaseState, LeaseTable, ProcessKind, ProcessRecord
from waggle.clock import FakeClock
from waggle.ids import new_lease_id
from waggle.messages.cell import ReleaseCause
from waggle.messages.labels import AccessLevel

CLOCK = FakeClock()


def _lease(**changes: object) -> DeviceLease:
    """Build an open lease with a fresh id."""
    state = sample_state(CLOCK)
    values: dict[str, object] = {
        "lease_id": new_lease_id(CLOCK),
        "cell_id": state.cell_id,
        "holder": state.warden_id,
        "access_level": AccessLevel.FULL,
        "scratch_root": "/data/leases/x",
        "quota_bytes": 10,
        "opened_at": CLOCK.now(),
    }
    values.update(changes)
    return DeviceLease(**values)


async def test_a_lease_put_in_the_table_survives_a_reload(tmp_path: Path) -> None:
    path = tmp_path / "leases.json"
    table = LeaseTable(path)
    lease = _lease()
    await table.put(lease)
    assert LeaseTable.load(path).get(lease.lease_id) == lease
    assert table.open_ids() == (lease.lease_id,)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
async def test_the_table_file_is_private(tmp_path: Path) -> None:
    table = LeaseTable(tmp_path / "leases.json")
    await table.put(_lease())
    assert stat.S_IMODE((tmp_path / "leases.json").stat().st_mode) == 0o600


async def test_a_missing_file_is_an_empty_table(tmp_path: Path) -> None:
    assert LeaseTable.load(tmp_path / "absent.json").all() == ()


async def test_a_read_refused_while_the_packet_replaces_the_file_is_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two refusals and then the file, as Windows answers a read beside a running replace.
    path = tmp_path / "leases.json"
    lease = _lease()
    await LeaseTable(path).put(lease)
    real = Path.read_bytes
    refusals = iter((True, True))

    def read_bytes(self: Path) -> bytes:
        if self == path and next(refusals, False):
            raise PermissionError("the file is in use by another handle")
        return real(self)

    monkeypatch.setattr("pollen.state.SHARED_FILE_RETRY_S", 0.0)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    assert LeaseTable.load(path).get(lease.lease_id) == lease


async def test_modifying_a_lease_writes_the_result_and_returns_it(tmp_path: Path) -> None:
    table = LeaseTable(tmp_path / "leases.json")
    lease = _lease()
    await table.put(lease)
    record = ProcessRecord(pid=5, created=1.0, kind=ProcessKind.EXEC)
    changed = await table.modify(lease.lease_id, lambda held: held.with_process(record))
    assert changed.processes == (record,)
    assert LeaseTable.load(tmp_path / "leases.json").process_count() == 1
    with pytest.raises(LeaseNotOpenError):
        await table.modify(new_lease_id(CLOCK), lambda held: held)


async def test_removing_a_lease_forgets_it_and_an_unknown_one_is_a_no_op(tmp_path: Path) -> None:
    table = LeaseTable(tmp_path / "leases.json")
    lease = _lease()
    await table.put(lease)
    await table.remove(lease.lease_id)
    await table.remove(lease.lease_id)
    assert LeaseTable.load(tmp_path / "leases.json").all() == ()


async def test_only_an_open_lease_with_an_open_session_takes_session_traffic(
    tmp_path: Path,
) -> None:
    table = LeaseTable(tmp_path / "leases.json")
    open_lease, closed, releasing = _lease(), _lease(is_session_closed=True), _lease()
    for lease in (open_lease, closed, releasing.releasing(ReleaseCause.ORPHAN_SWEEP)):
        await table.put(lease)
    assert table.require_session(open_lease.lease_id) == open_lease
    for refused in (closed.lease_id, releasing.lease_id, new_lease_id(CLOCK)):
        with pytest.raises(LeaseNotOpenError):
            table.require_session(refused)
    assert set(table.open_ids()) == {open_lease.lease_id, closed.lease_id}
    assert {lease.state for lease in table.all()} == {LeaseState.OPEN, LeaseState.RELEASING}


async def test_a_failed_write_leaves_the_table_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = LeaseTable(tmp_path / "leases.json")
    kept = _lease()
    await table.put(kept)

    def refuse(*_: object, **__: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("pollen.lease.table.write_atomic", refuse)
    with pytest.raises(StateError):
        await table.put(_lease())
    with pytest.raises(StateError):
        await table.remove(kept.lease_id)
    assert table.all() == (kept,)
    assert LeaseTable.load(tmp_path / "leases.json").all() == (kept,)


@pytest.mark.parametrize("content", [b"{not json", b'{"leases": [{"surprise": 1}]}', b"[]"])
def test_a_damaged_table_is_a_state_error(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "leases.json"
    path.write_bytes(content)
    with pytest.raises(StateError):
        LeaseTable.load(path)


def test_an_unreadable_table_is_a_state_error(tmp_path: Path) -> None:
    (tmp_path / "leases.json").mkdir()
    with pytest.raises(StateError):
        LeaseTable.load(tmp_path / "leases.json")
