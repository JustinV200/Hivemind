"""Tests for hivemind.basket.store.files: atomic writes, verified reads, no path escape.

Fits into the Hive:
    Mirrors src/hivemind/basket/store/files.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.store.files for the module under test.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from hivemind.basket.errors import BlobIntegrityError, BlobNotFoundError
from hivemind.basket.store.files import BlobFiles

_DATA = b"some blob bytes"
_SHA = hashlib.sha256(_DATA).hexdigest()


def test_a_blob_lives_at_root_slash_two_hex_digits_slash_the_hash(tmp_path: Path) -> None:
    files = BlobFiles(tmp_path)

    assert files.path_for(_SHA) == tmp_path / _SHA[:2] / _SHA


def test_write_then_read_returns_the_bytes_and_leaves_no_part_file(tmp_path: Path) -> None:
    files = BlobFiles(tmp_path)

    wrote = files.write(_SHA, _DATA)

    assert wrote and files.read(_SHA) == _DATA
    assert not list(tmp_path.rglob("*.part"))


def test_a_second_write_of_a_verified_copy_does_not_rewrite_the_file(tmp_path: Path) -> None:
    files = BlobFiles(tmp_path)
    files.write(_SHA, _DATA)
    before = files.path_for(_SHA).stat()

    wrote = files.write(_SHA, _DATA)

    after = files.path_for(_SHA).stat()
    assert not wrote
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


def test_a_corrupt_copy_is_overwritten_by_the_next_write(tmp_path: Path) -> None:
    files = BlobFiles(tmp_path)
    files.write(_SHA, _DATA)
    files.path_for(_SHA).write_bytes(b"rotten")

    wrote = files.write(_SHA, _DATA)

    assert wrote and files.read(_SHA) == _DATA


def test_a_stale_part_file_from_a_crashed_write_is_replaced(tmp_path: Path) -> None:
    files = BlobFiles(tmp_path)
    target = files.path_for(_SHA)
    target.parent.mkdir(parents=True)
    (target.parent / f".{_SHA}.part").write_bytes(b"half a wr")

    files.write(_SHA, _DATA)

    assert files.read(_SHA) == _DATA
    assert not list(tmp_path.rglob("*.part"))


def test_reading_bytes_that_no_longer_hash_to_their_name_is_an_integrity_error(
    tmp_path: Path,
) -> None:
    files = BlobFiles(tmp_path)
    files.write(_SHA, _DATA)
    files.path_for(_SHA).write_bytes(b"tampered")

    with pytest.raises(BlobIntegrityError, match="hash"):
        files.read(_SHA)


def test_reading_a_missing_file_is_an_integrity_error_not_a_crash(tmp_path: Path) -> None:
    with pytest.raises(BlobIntegrityError, match="missing"):
        BlobFiles(tmp_path).read(_SHA)


@pytest.mark.parametrize("bad", ["../escape", "A" * 64, "ab", "", "a" * 63 + "/"])
def test_a_hash_that_is_not_a_digest_never_becomes_a_path(tmp_path: Path, bad: str) -> None:
    with pytest.raises(BlobNotFoundError):
        BlobFiles(tmp_path).path_for(bad)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows files carry no POSIX mode bits")
def test_the_files_are_owner_only(tmp_path: Path) -> None:
    files = BlobFiles(tmp_path)
    files.write(_SHA, _DATA)

    assert files.path_for(_SHA).stat().st_mode & 0o077 == 0
