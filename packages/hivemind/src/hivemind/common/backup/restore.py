"""Put an opened Hive backup back: the database through SQLite's backup API, the trees file by file.

The destination database is written the same way the source was read: SQLite's online backup API
copies the checked snapshot's pages into a connection on the destination file, so SQLite itself
rewrites the file and its `-wal` and `-shm` siblings consistently. Copying the snapshot over
`hive.sqlite3` by hand would leave a stale `-wal` from the old database beside it, which SQLite
would then replay over the restored pages. Each tree's files are copied into the destination tree
atomically; a file already there with the right digest is left alone (a content-addressed blob
that never changed), and files the backup does not list are left too, so restoring never deletes.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Called by `hive restore`
    (`hivemind.cli.backup`) after `hivemind.common.backup.read.open_backup` checked the archive,
    while the caller holds the Hive's serve and Queen locks so nothing else has the file open.

Key invariants:
    - Only an `OpenedBackup` is restored: one whose every member matched its index.
    - A tree the backup carries but the caller gave no destination for is refused before the
      database is touched.

See Also:
    - hivemind.common.backup.write for the matching snapshot.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path

from hivemind.common.backup.digest import copy_hashed, sha256_file
from hivemind.common.backup.index import DB_MEMBER
from hivemind.common.backup.read import OpenedBackup
from hivemind.common.errors import BackupError
from hivemind.common.sqlite import connect

__all__ = ["restore_backup"]


def restore_backup(opened: OpenedBackup, db: Path, trees: Mapping[str, Path]) -> int:
    """Replace ``db`` with the backup's snapshot and copy its trees into ``trees``.

    Blocking (disk); the caller must hold the Hive's locks so no Queen or Entrance has ``db``
    open while its pages are replaced.

    Args:
        opened: The checked, extracted backup.
        db: The destination database; created if missing, replaced page by page if present.
        trees: Where each of the backup's trees goes, by name.

    Returns:
        How many tree files were written (files already present with the right digest are not).

    Raises:
        BackupError: The backup carries a tree ``trees`` gives no destination for.
        OSError: A destination cannot be written.
    """
    # Checked first so a refused restore leaves the destination database untouched.
    unmapped = sorted(set(opened.index.trees) - set(trees))
    if unmapped:
        raise BackupError(f"No destination was given for the backup's tree {unmapped[0]!r}.")
    db.parent.mkdir(parents=True, exist_ok=True)
    # The destination opened the Hive's standard way (WAL, busy timeout), so the restored file is
    # in the mode every store expects.
    with closing(sqlite3.connect(str(opened.db))) as snapshot, closing(connect(db)) as target:
        # Latency: one pass over every page of the snapshot.
        snapshot.backup(target)
    return sum(_restore_file(opened, path, sha, trees) for path, sha in _tree_files(opened))


def _tree_files(opened: OpenedBackup) -> list[tuple[str, str]]:
    """Every tree member of the backup with its digest; the database is not one."""
    return [(f.path, f.sha256) for f in opened.index.files if f.path != DB_MEMBER]


def _restore_file(opened: OpenedBackup, member: str, sha256: str, trees: Mapping[str, Path]) -> int:
    """Copy one tree member into place unless an identical file is there; 1 if written."""
    tree, _, relative = member.partition("/")
    target = trees[tree].joinpath(*relative.split("/"))
    # An identical file is already the restored state; rewriting it would only cost disk time.
    if target.is_file() and sha256_file(target) == sha256:
        return 0
    with (opened.root / member).open("rb") as stream:
        copy_hashed(stream, target)
    return 1
