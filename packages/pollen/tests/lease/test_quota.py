"""Test the scratch quota: measuring a directory without trusting a link, and refusing a big write.

A lease may ask for a scratch quota (gateway-contract section 9.1), and the Pollen Packet (the thin
gateway program on a device the Hive borrows) enforces it as the Hive Stand does for its own
leases: it measures the size of the lease's scratch directory *never following a symbolic link*, so
a link can neither hide bytes nor inflate the figure, and it refuses a write that would take the
directory past the quota or leave the device's disk with too little free. These tests measure real
directories.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.lease.quota.

Key invariants:
    - A symbolic link counts as nothing, whatever it points at.
    - A directory that vanishes mid-walk counts as zero for the part that is gone.

See Also:
    - docs/waggle/gateway-contract.md section 9.1.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pollen.errors import QuotaExceededError
from pollen.lease import check_room, directory_size_bytes


def test_the_size_is_the_sum_of_the_regular_files_at_any_depth(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "one.bin").write_bytes(b"x" * 10)
    (tmp_path / "a" / "two.bin").write_bytes(b"x" * 20)
    (tmp_path / "a" / "b" / "three.bin").write_bytes(b"x" * 30)
    assert directory_size_bytes(tmp_path) == 60


def test_a_missing_directory_weighs_nothing(tmp_path: Path) -> None:
    assert directory_size_bytes(tmp_path / "absent") == 0


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links")
def test_a_link_is_never_followed_so_it_can_neither_hide_nor_inflate_usage(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge.bin").write_bytes(b"x" * 1000)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "real.bin").write_bytes(b"x" * 5)
    (scratch / "dir-link").symlink_to(outside, target_is_directory=True)
    (scratch / "file-link").symlink_to(outside / "huge.bin")
    (scratch / "loop").symlink_to(scratch, target_is_directory=True)
    assert directory_size_bytes(scratch) == 5


def test_a_write_within_the_quota_and_the_disk_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "used.bin").write_bytes(b"x" * 10)
    check_room(tmp_path, quota_bytes=100, incoming=90, lease_id="lease_x")


def test_a_write_past_the_quota_is_refused_and_names_the_numbers(tmp_path: Path) -> None:
    (tmp_path / "used.bin").write_bytes(b"x" * 10)
    with pytest.raises(QuotaExceededError, match=r"91 bytes.*from 10 bytes.*quota of 100"):
        check_room(tmp_path, quota_bytes=100, incoming=91, lease_id="lease_x")


def test_a_write_that_would_fill_the_disk_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Usage:
        total, used, free = 1_000_000, 999_000, 1_000

    monkeypatch.setattr("pollen.lease.quota.shutil.disk_usage", lambda _path: Usage())
    with pytest.raises(QuotaExceededError, match="less than"):
        check_room(tmp_path, quota_bytes=10**9, incoming=1, lease_id="lease_x")


def test_a_disk_that_cannot_be_measured_leaves_the_quota_as_the_only_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_path: Path) -> object:
        raise OSError("no such device")

    monkeypatch.setattr("pollen.lease.quota.shutil.disk_usage", broken)
    check_room(tmp_path, quota_bytes=100, incoming=1, lease_id="lease_x")
    with pytest.raises(QuotaExceededError):
        check_room(tmp_path, quota_bytes=0, incoming=1, lease_id="lease_x")
