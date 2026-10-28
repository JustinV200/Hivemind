"""Tests for hivemind.common.backup.restore: a backup put back over a Hive that moved on.

Fits into the Hive:
    Mirrors src/hivemind/common/backup/restore.py (codingrules section 3). Every database here is
    a temp-dir file; nothing touches a real Hive's hive.sqlite3.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.common.backup import (
    BackupSource,
    OpenedBackup,
    open_backup,
    restore_backup,
    write_backup,
)
from hivemind.common.errors import BackupError
from hivemind.common.sqlite import connect
from waggle.clock import FakeClock
from waggle.ids import new_hive_id


def _opened(tmp_path: Path) -> tuple[Path, Path, OpenedBackup]:
    """Back up a Hive with one row and one blob, then let it move on: db, blobs, backup."""
    db = tmp_path / "hive" / "hive.sqlite3"
    db.parent.mkdir()
    blobs = tmp_path / "hive" / "basket"
    (blobs / "ab").mkdir(parents=True)
    (blobs / "ab" / "abcd").write_bytes(b"blob")
    live = connect(db)
    live.execute("CREATE TABLE t (v TEXT)")
    live.execute("INSERT INTO t VALUES ('before')")
    clock = FakeClock()
    source = BackupSource(db, new_hive_id(clock), {"basket": blobs})
    receipt = write_backup(source, tmp_path / "hive.tar.gz", clock)
    # The Hive moves on after the backup: a new row (still in its -wal) and a lost blob.
    live.execute("INSERT INTO t VALUES ('after')")
    live.close()
    (blobs / "ab" / "abcd").unlink()
    work = tmp_path / "work"
    work.mkdir()
    return db, blobs, open_backup(receipt.path, work)


def test_restore_puts_back_the_snapshot_and_the_lost_files(tmp_path: Path) -> None:
    db, blobs, opened = _opened(tmp_path)
    (blobs / "kept").write_bytes(b"not in the backup")

    written = restore_backup(opened, db, {"basket": blobs})
    again = restore_backup(opened, db, {"basket": blobs})

    restored = connect(db)
    assert [row[0] for row in restored.execute("SELECT v FROM t")] == ["before"]
    assert restored.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    restored.close()
    assert (blobs / "ab" / "abcd").read_bytes() == b"blob"
    assert (blobs / "kept").exists()  # A restore never deletes.
    assert (written, again) == (1, 0)


def test_a_tree_with_no_destination_is_refused_before_the_database_is_touched(
    tmp_path: Path,
) -> None:
    db, _, opened = _opened(tmp_path)

    with pytest.raises(BackupError):
        restore_backup(opened, db, {})

    still = connect(db)
    assert len(still.execute("SELECT v FROM t").fetchall()) == 2
    still.close()
