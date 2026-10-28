"""Tests for hivemind.common.backup.read: a hostile or damaged archive is refused, whole.

Fits into the Hive:
    Mirrors src/hivemind/common/backup/read.py (codingrules section 3). A security-boundary
    table (AGENTS.md section 6): each row is one way an archive can lie, and every row must be
    refused without writing outside the work directory.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hivemind.common.backup import DB_MEMBER, INDEX_MEMBER, BackupSource, open_backup, write_backup
from hivemind.common.errors import BackupError
from hivemind.common.sqlite import connect
from waggle.clock import FakeClock
from waggle.ids import new_hive_id

Members = list[tuple[str, bytes | None]]  # None: a symlink member to /etc/passwd.


def _backup(tmp_path: Path) -> tuple[dict[str, Any], Members]:
    """A real backup's index (as a dict) and members, for a row to tamper with."""
    db = tmp_path / "src" / "hive.sqlite3"
    db.parent.mkdir()
    connection = connect(db)
    connection.execute("CREATE TABLE t (v TEXT)")
    connection.close()
    blobs = tmp_path / "src" / "basket"
    blobs.mkdir()
    (blobs / "blob").write_bytes(b"blob")
    clock = FakeClock()
    source = BackupSource(db, new_hive_id(clock), {"basket": blobs})
    receipt = write_backup(source, tmp_path / "good.tar.gz", clock)
    members: Members = []
    with tarfile.open(receipt.path, "r:gz") as tar:
        for member in tar.getmembers():
            stream = tar.extractfile(member)
            assert stream is not None
            members.append((member.name, stream.read()))
    index = json.loads(tar_body) if (tar_body := members[0][1]) is not None else {}
    return index, members[1:]


def _pack(path: Path, index: dict[str, Any] | None, members: Members) -> None:
    """Write a tar.gz with ``index`` first (when given) and then ``members``, as given."""
    with tarfile.open(path, "w:gz") as tar:
        body = [(INDEX_MEMBER, json.dumps(index).encode())] if index is not None else []
        for name, data in [*body, *members]:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
                continue
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _relist(index: dict[str, Any], name: str, data: bytes) -> None:
    """Make the index describe ``data`` under ``name``, as a careful forger would."""
    entry = {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    index["files"] = [f for f in index["files"] if f["path"] != name] + [entry]


def _traversal(index: dict[str, Any], members: Members) -> Members:
    _relist(index, "basket/../../escaped", b"x")
    return [*members, ("basket/../../escaped", b"x")]


def _extra(index: dict[str, Any], members: Members) -> Members:
    return [*members, ("basket/unlisted", b"x")]


def _missing(index: dict[str, Any], members: Members) -> Members:
    return members[:1]


def _swapped(index: dict[str, Any], members: Members) -> Members:
    return [(name, b"blub" if name == "basket/blob" else data) for name, data in members]


def _duplicate(index: dict[str, Any], members: Members) -> Members:
    return [*members, members[-1]]


def _case_twin(index: dict[str, Any], members: Members) -> Members:
    _relist(index, "basket/BLOB", b"blob")
    return [*members, ("basket/BLOB", b"blob")]


def _dot_twin(index: dict[str, Any], members: Members) -> Members:
    # Windows drops a trailing dot, so this would overwrite basket/blob once extracted.
    _relist(index, "basket/blob.", b"evil")
    return [*members, ("basket/blob.", b"evil")]


def _device_name(index: dict[str, Any], members: Members) -> Members:
    _relist(index, "basket/nul", b"x")
    return [*members, ("basket/nul", b"x")]


def _hostile_secret_name(index: dict[str, Any], members: Members) -> Members:
    # A restore may print the names it lacks: no terminal control sequence may get that far.
    index["secret_names"] = ["[2Jowned"]
    return members


def _corrupt_db(index: dict[str, Any], members: Members) -> Members:
    garbage = b"SQLite format 3\x00" + b"\xff" * 4080
    _relist(index, DB_MEMBER, garbage)
    return [(DB_MEMBER, garbage), *members[1:]]


def _symlink(index: dict[str, Any], members: Members) -> Members:
    return [(name, None if name == "basket/blob" else data) for name, data in members]


def _resized(index: dict[str, Any], members: Members) -> Members:
    for entry in index["files"]:
        entry["size"] += 1
    return members


def _no_database(index: dict[str, Any], members: Members) -> Members:
    index["files"] = index["files"][1:]
    return members[1:]


def _undeclared_tree(index: dict[str, Any], members: Members) -> Members:
    index["trees"] = []
    return members


Tamper = Callable[[dict[str, Any], Members], Members]


@pytest.mark.parametrize(
    "tamper",
    [
        _traversal,
        _extra,
        _missing,
        _swapped,
        _duplicate,
        _case_twin,
        _dot_twin,
        _device_name,
        _hostile_secret_name,
        _corrupt_db,
        _symlink,
        _resized,
        _no_database,
        _undeclared_tree,
    ],
)
def test_a_tampered_archive_is_refused_and_nothing_lands_outside_workdir(
    tmp_path: Path, tamper: Tamper
) -> None:
    index, members = _backup(tmp_path)
    members = tamper(index, members)
    _pack(tmp_path / "bad.tar.gz", index, members)
    work = tmp_path / "work"
    work.mkdir()

    with pytest.raises(BackupError):
        open_backup(tmp_path / "bad.tar.gz", work)

    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize("case", ["no index first", "not an archive"])
def test_an_archive_of_the_wrong_shape_is_refused(tmp_path: Path, case: str) -> None:
    _, members = _backup(tmp_path)
    bad = tmp_path / "bad.tar.gz"
    if case == "no index first":
        _pack(bad, None, members)
    else:
        bad.write_bytes(b"not a tar file at all")

    with pytest.raises(BackupError):
        open_backup(bad, tmp_path)


def test_the_expected_archive_checksum_is_enforced(tmp_path: Path) -> None:
    _backup(tmp_path)
    work = tmp_path / "work"
    work.mkdir()

    with pytest.raises(BackupError):
        open_backup(tmp_path / "good.tar.gz", work, expect_sha256="0" * 64)
    opened = open_backup(tmp_path / "good.tar.gz", work)

    assert open_backup(tmp_path / "good.tar.gz", tmp_path / "again", opened.sha256.upper())
