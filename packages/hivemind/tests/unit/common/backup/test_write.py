"""Tests for hivemind.common.backup.write: a consistent snapshot of a live database, and refusals.

Fits into the Hive:
    Mirrors src/hivemind/common/backup/write.py (codingrules section 3). Every database here is a
    temp-dir file; nothing touches a real Hive's hive.sqlite3.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest

from hivemind.common.backup import DB_MEMBER, INDEX_MEMBER, BackupSource, sha256_file, write_backup
from hivemind.common.errors import BackupError
from hivemind.common.sqlite import connect
from waggle.clock import FakeClock
from waggle.ids import new_hive_id


def test_a_live_wal_database_and_its_tree_are_snapshotted_with_checksums(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    live = connect(db)
    live.execute("CREATE TABLE t (v TEXT)")
    live.execute("INSERT INTO t VALUES ('kept')")
    blobs = tmp_path / "basket"
    (blobs / "ab").mkdir(parents=True)
    (blobs / "ab" / "abcd").write_bytes(b"blob")
    (blobs / "ab" / ".abcd.part").write_bytes(b"half")  # An in-flight write: never archived.
    clock = FakeClock()
    source = BackupSource(
        db, new_hive_id(clock), {"basket": blobs}, "0" * 64, ("hive.ed25519", "console.key")
    )

    # The live connection stays open, its commit still in the -wal file, while the backup runs.
    receipt = write_backup(source, tmp_path / "out" / "hive.tar.gz", clock)

    assert receipt.sha256 == sha256_file(receipt.path)
    assert [f.path for f in receipt.index.files] == [DB_MEMBER, "basket/ab/abcd"]
    assert receipt.index.secret_names == ("console.key", "hive.ed25519")
    with tarfile.open(receipt.path, "r:gz") as tar:
        assert tar.getnames()[0] == INDEX_MEMBER
        snapshot = tar.extractfile(DB_MEMBER)
        assert snapshot is not None
        (tmp_path / "snap.sqlite3").write_bytes(snapshot.read())
    copy = sqlite3.connect(tmp_path / "snap.sqlite3")
    assert copy.execute("SELECT v FROM t").fetchall() == [("kept",)]
    copy.close()
    live.close()
    # Nothing but the archive was left beside it: no work directory, no .part file.
    assert [p.name for p in receipt.path.parent.iterdir()] == ["hive.tar.gz"]


@pytest.mark.parametrize("case", ["no database", "output exists"])
def test_write_refuses_without_creating_or_overwriting_anything(tmp_path: Path, case: str) -> None:
    db = tmp_path / "hive.sqlite3"
    out = tmp_path / "hive.tar.gz"
    if case == "output exists":
        connect(db).close()
        out.write_bytes(b"an earlier backup")
    clock = FakeClock()

    with pytest.raises(BackupError):
        write_backup(BackupSource(db, new_hive_id(clock)), out, clock)

    assert db.exists() == (case == "output exists")
    assert not out.exists() or out.read_bytes() == b"an earlier backup"
