"""Test leaving a device as found: record before changing, restore newest first, remove scratch.

A Real Cell (an existing device the Hive borrows) is left exactly as found (gateway-contract
section 12). Before the Pollen Packet (the thin gateway program on the device) first changes a path
outside scratch it records the path's prior state in the lease table: that it did not exist, or its
bytes (copied aside) and mode, and the directories the change would create. If it cannot record, it
refuses the change. A release undoes the records newest first, deletes what the lease created,
writes back what it changed, and removes scratch, reporting any path it could not put back. These
tests change real files in a temporary directory and check what the release leaves.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.lease.restore.

Key invariants:
    - Nothing outside scratch is changed that was not recorded first.
    - A path changed twice is restored to its state before the *first* change.
    - Backups are kept until every path is back.

See Also:
    - docs/waggle/gateway-contract.md section 12.
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest
from states import sample_state

from pollen.errors import PathRefusedError, QuotaExceededError
from pollen.lease import (
    DeviceLease,
    LeaseTable,
    RestoreKind,
    RestoreRecord,
    record_before_change,
    remove_scratch,
    replay_restores,
)
from pollen.state import DataLayout
from waggle.clock import FakeClock
from waggle.ids import new_lease_id
from waggle.messages.labels import AccessLevel

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
CLOCK = FakeClock()


class Rig:
    """A lease in a table over a temporary data directory."""

    def __init__(self, tmp_path: Path) -> None:
        """Create the data layout and an empty table; ``open`` stores a lease."""
        self.layout = DataLayout(tmp_path / "data")
        self.layout.ensure()
        self.table = LeaseTable(self.layout.lease_table)
        self.outside = tmp_path / "outside"
        self.outside.mkdir()

    async def open(self) -> DeviceLease:
        """Store a fresh open lease and return it."""
        state = sample_state(CLOCK)
        lease_id = new_lease_id(CLOCK)
        scratch = self.layout.scratch(lease_id)
        scratch.mkdir(parents=True)
        lease = DeviceLease(
            lease_id=lease_id,
            cell_id=state.cell_id,
            holder=state.warden_id,
            access_level=AccessLevel.FULL,
            scratch_root=str(scratch),
            quota_bytes=1_000_000,
            opened_at=CLOCK.now(),
        )
        await self.table.put(lease)
        return lease

    async def record(self, lease: DeviceLease, target: Path) -> DeviceLease:
        """Record ``target`` and return the lease as stored afterwards."""
        stored = self.table.get(lease.lease_id)
        assert stored is not None
        await record_before_change(self.table, self.layout, stored, target)
        updated = self.table.get(lease.lease_id)
        assert updated is not None
        return updated


async def test_a_new_file_and_the_directories_it_needs_are_recorded_and_removed(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    target = rig.outside / "a" / "b" / "new.txt"
    recorded = await rig.record(lease, target)
    assert [r.kind for r in recorded.restores] == [
        RestoreKind.CREATED_DIR,
        RestoreKind.CREATED_DIR,
        RestoreKind.CREATED_FILE,
    ]
    target.parent.mkdir(parents=True)
    target.write_text("made by the lease")
    assert replay_restores(rig.layout, recorded) == []
    assert not (rig.outside / "a").exists() and rig.outside.exists()


async def test_a_replaced_file_gets_its_bytes_and_mode_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    target = rig.outside / "keep.txt"
    target.write_bytes(b"original")
    target.chmod(0o640)
    recorded = await rig.record(lease, target)
    target.write_bytes(b"changed")
    target.chmod(0o600)
    assert replay_restores(rig.layout, recorded) == []
    assert target.read_bytes() == b"original"
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not rig.layout.backups(lease.lease_id).exists()


async def test_a_deleted_file_comes_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    target = rig.outside / "precious.txt"
    target.write_bytes(b"do not lose")
    recorded = await rig.record(lease, target)
    target.unlink()
    assert replay_restores(rig.layout, recorded) == []
    assert target.read_bytes() == b"do not lose"


async def test_a_path_changed_twice_is_restored_to_before_the_first_change(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    target = rig.outside / "twice.txt"
    target.write_bytes(b"v0")
    first = await rig.record(lease, target)
    target.write_bytes(b"v1")
    second = await rig.record(lease, target)  # Already recorded: no new record, no new backup.
    target.write_bytes(b"v2")
    assert second.restores == first.restores
    assert replay_restores(rig.layout, second) == []
    assert target.read_bytes() == b"v0"


async def test_a_directory_cannot_be_recorded_as_a_file(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    with pytest.raises(PathRefusedError):
        await rig.record(lease, rig.outside)


async def test_a_file_too_large_to_record_refuses_the_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    big = rig.outside / "big.bin"
    big.write_bytes(b"x" * 100)
    monkeypatch.setattr("pollen.lease.restore.MAX_BACKUP_BYTES", 10)
    with pytest.raises(QuotaExceededError, match="refused"):
        await rig.record(lease, big)
    assert big.read_bytes() == b"x" * 100


async def test_too_many_changed_paths_refuse_the_next_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    monkeypatch.setattr("pollen.lease.restore.MAX_RESTORE_RECORDS", 2)
    await rig.record(lease, rig.outside / "one.txt")
    await rig.record(lease, rig.outside / "two.txt")
    with pytest.raises(QuotaExceededError, match="no more can be recorded"):
        await rig.record(lease, rig.outside / "three.txt")


async def test_a_path_whose_state_cannot_be_read_refuses_the_change(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    plain = rig.outside / "plain.txt"
    plain.write_text("x")
    with pytest.raises(QuotaExceededError, match="could not be read"):
        await rig.record(
            lease, plain / "below-a-file"
        )  # Not a directory: stat fails, not "absent".


async def test_a_file_whose_bytes_cannot_be_saved_refuses_the_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    target = rig.outside / "unsaveable.txt"
    target.write_bytes(b"closed")

    def refuse(*_: object) -> None:
        raise OSError("no room")

    monkeypatch.setattr("pollen.lease.restore.shutil.copyfile", refuse)
    with pytest.raises(QuotaExceededError, match="could not be saved"):
        await rig.record(lease, target)


async def test_a_path_that_cannot_be_restored_is_reported_and_its_backup_kept(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    target = rig.outside / "stuck.txt"
    target.write_bytes(b"original")
    recorded = await rig.record(lease, target)
    target.unlink()
    target.mkdir()  # Something now stands where the file must go back.
    assert replay_restores(rig.layout, recorded) == [str(target)]
    assert rig.layout.backups(lease.lease_id).exists()


async def test_removing_scratch_takes_everything_including_read_only_files(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    (scratch / "deep" / "er").mkdir(parents=True)
    locked = scratch / "deep" / "er" / "readonly.txt"
    locked.write_text("x")
    locked.chmod(0o400)
    assert remove_scratch(scratch) is True
    assert not scratch.exists()
    assert remove_scratch(scratch) is True  # Already gone: nothing to do, nothing wrong.


@posix_only
async def test_a_link_in_place_of_scratch_is_removed_and_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "precious"
    target.mkdir()
    (target / "file.txt").write_text("keep")
    link = tmp_path / "scratch"
    link.symlink_to(target, target_is_directory=True)
    assert remove_scratch(link) is True
    assert not link.exists() and (target / "file.txt").read_text() == "keep"


async def test_scratch_that_cannot_be_removed_is_reported_after_a_few_tries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pollen.lease.restore.SCRATCH_REMOVE_RETRY_S", 0.0)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    attempts: list[int] = []

    def refuse(*_: object, **__: object) -> None:
        attempts.append(1)
        raise OSError("busy")

    monkeypatch.setattr("pollen.lease.restore.shutil.rmtree", refuse)
    assert remove_scratch(scratch) is False
    assert len(attempts) == 5


async def test_a_directory_the_lease_made_that_is_already_gone_is_not_a_problem(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    recorded = await rig.record(await rig.open(), rig.outside / "gone" / "new.txt")
    assert replay_restores(rig.layout, recorded) == []  # Nothing was ever made: nothing to undo.


async def test_a_replaced_file_without_a_saved_mode_is_given_its_bytes_and_keeps_its_mode(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    lease = await rig.open()
    target = rig.outside / "keep.txt"
    target.write_text("changed")
    backups = rig.layout.backups(lease.lease_id)
    backups.mkdir(parents=True)
    (backups / "0.bak").write_text("original")
    record = RestoreRecord(path=str(target), kind=RestoreKind.REPLACED, backup="0.bak", mode=None)
    assert replay_restores(rig.layout, lease.model_copy(update={"restores": (record,)})) == []
    assert target.read_text() == "original"


def test_a_read_only_file_is_made_writable_and_scratch_still_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    stuck = scratch / "stuck.txt"
    stuck.write_text("x")
    real_rmtree = shutil.rmtree

    def refuse_once(path: Path, onexc: object) -> None:
        # What a platform does for a read-only file: the removal reports PermissionError.
        assert callable(onexc)
        onexc(os.unlink, str(stuck), PermissionError("read-only"))
        real_rmtree(path)

    monkeypatch.setattr("pollen.lease.restore.shutil.rmtree", refuse_once)
    assert remove_scratch(scratch) is True and not scratch.exists()


def test_a_removal_that_keeps_failing_is_reported_after_its_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def busy(_path: Path, onexc: object) -> None:
        assert callable(onexc)
        onexc(os.unlink, str(scratch), OSError("busy"))  # Not a permission problem: it re-raises.

    monkeypatch.setattr("pollen.lease.restore.shutil.rmtree", busy)
    monkeypatch.setattr("pollen.lease.restore.SCRATCH_REMOVE_RETRY_S", 0.0)
    assert remove_scratch(scratch) is False and scratch.exists()
