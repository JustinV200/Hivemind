"""Write a Hive backup: a consistent database snapshot and file trees in one checksummed archive.

The database is copied with SQLite's online backup API (`sqlite3.Connection.backup`), never by
copying the file: a live `hive.sqlite3` in WAL mode keeps recent commits in its `-wal` sibling,
so a file copy taken while the Queen runs can be torn or stale, while the backup API reads one
consistent snapshot through SQLite itself and runs fine beside a writer. The snapshot is switched
to rollback journalling so it is one self-contained file. Each named tree (the Basket's blob
directory) is copied file by file into a work directory, hashing as it goes, so the checksum is
of exactly the bytes archived. Then the index and every member go into a gzip'd tar written
beside the output and renamed into place, so the output path only ever holds a whole archive.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Called by `hive backup`
    (`hivemind.cli.backup`), and by Supersedure's copy (roadmap 13.2a) when it lands. Calls into
    the standard library's `sqlite3` and `tarfile` and this package's index and digest.

Key invariants:
    - The live database is only ever opened, read through the backup API and closed: no pragma
      is set on it and no file beside it is touched.
    - An existing output is never overwritten, and a failed write leaves no output behind.
    - The secret store is never read here; the caller passes the identity key's public half and
      the secret names, which is all an index holds.

See Also:
    - docs/adr/0064-backups-are-one-checksummed-archive-without-secrets.md.
    - hivemind.common.backup.read for the reader that checks all of it.
"""

from __future__ import annotations

import io
import os
import sqlite3
import tarfile
import tempfile
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from hivemind.common.backup.digest import copy_hashed, sha256_file
from hivemind.common.backup.index import (
    DB_MEMBER,
    FORMAT_VERSION,
    INDEX_MEMBER,
    BackupFile,
    BackupIndex,
    check_member_path,
)
from hivemind.common.errors import BackupError
from waggle.clock import Clock

ARCHIVE_MODE = 0o600  # The archive holds every store: owner only (POSIX).

__all__ = ["BackupReceipt", "BackupSource", "write_backup"]


@dataclass(frozen=True, slots=True)
class BackupSource:
    """What one backup copies, and the facts about the Hive its index records.

    Attributes:
        db: The Hive's live database file.
        hive_id: The Hive the database belongs to.
        trees: Extra file trees to carry, by name (e.g. ``{"basket": <blob root>}``); a tree
            whose directory does not exist is recorded as empty.
        identity_key: The public half of the Hive's identity key, hex, or None.
        secret_names: The names in the Hive's secret store, never their values.
    """

    db: Path
    hive_id: str
    trees: Mapping[str, Path] = field(default_factory=dict)
    identity_key: str | None = None
    secret_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BackupReceipt:
    """What a written backup is: where, its index, and the whole archive's own checksum.

    Attributes:
        path: The archive.
        index: Its `backup.json`.
        sha256: The archive file's SHA-256, for comparing a copy after it moves.
        size: The archive's length in bytes.
    """

    path: Path
    index: BackupIndex
    sha256: str
    size: int


def write_backup(source: BackupSource, out: Path, clock: Clock) -> BackupReceipt:
    """Snapshot the database and trees into a new checksummed archive at ``out``.

    Blocking (disk, and the database's read lock for the length of one copy); an async caller
    runs it under ``asyncio.to_thread``.

    Args:
        source: The database, trees and Hive facts to back up.
        out: The archive to create; must not exist yet.
        clock: Stamps the index's ``created_at``.

    Returns:
        The receipt: path, index, the archive's SHA-256 and size.

    Raises:
        BackupError: The database is missing, ``out`` already exists, or a tree holds a name a
            backup cannot carry.
        OSError: A file cannot be read or written.
    """
    # A missing database is refused rather than opened: sqlite3.connect would create an empty one.
    if not source.db.is_file():
        raise BackupError(f"There is no database at {source.db} to back up.")
    if out.exists():
        raise BackupError(f"{out} already exists; a backup never overwrites a file.")
    out.parent.mkdir(parents=True, exist_ok=True)
    # The work directory sits beside the output so the final rename never crosses file systems.
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".hive-backup-") as work:
        root = Path(work)
        created_at = clock.now()
        files = [_snapshot(source.db, root / DB_MEMBER)]
        for name, directory in sorted(source.trees.items()):
            files.extend(_copy_tree(name, directory, root))
        # model_validate: the plain hive id string is checked into a HiveId by the model itself.
        index = BackupIndex.model_validate(
            {
                "format": FORMAT_VERSION,
                "created_at": created_at,
                "hive_id": source.hive_id,
                "identity_key": source.identity_key,
                "secret_names": tuple(sorted(source.secret_names)),
                "trees": tuple(sorted(source.trees)),
                "files": tuple(files),
            }
        )
        _pack(index, root, out)
    return BackupReceipt(out, index, sha256_file(out), out.stat().st_size)


def _snapshot(db: Path, target: Path) -> BackupFile:
    """Copy the live database through SQLite's online backup API into one self-contained file."""
    # Plain connections, not hivemind.common.sqlite.connect: that one sets pragmas, and the live
    # file must not be changed by being backed up.
    with closing(sqlite3.connect(str(db))) as live, closing(sqlite3.connect(str(target))) as copy:
        # Latency: one pass over every page; WAL lets the Queen keep writing meanwhile.
        live.backup(copy)
        # The copy inherits WAL from the live header; rollback mode makes it one file, no -wal.
        copy.execute("PRAGMA journal_mode = DELETE")
    return BackupFile(path=DB_MEMBER, size=target.stat().st_size, sha256=sha256_file(target))


def _copy_tree(name: str, directory: Path, root: Path) -> list[BackupFile]:
    """Copy every regular file under ``directory`` into ``root/name``, hashing each on the way."""
    copied: list[BackupFile] = []
    # A tree that was never created (a Hive with no blob yet) is simply empty.
    if not directory.is_dir():
        return copied
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        # Hidden names are in-flight writes (`.<sha>.part`) and symlinks point outside the tree:
        # neither is the store's data.
        if any(part.startswith(".") for part in relative.parts) or path.is_symlink():
            continue
        if not path.is_file():
            continue
        member = f"{name}/{relative.as_posix()}"
        check_member_path(member, (name,))
        try:
            with path.open("rb") as stream:
                digest, size = copy_hashed(stream, root / member)
        except FileNotFoundError:
            # Removed between the listing and the copy (a blob evicted meanwhile): not data now.
            continue
        copied.append(BackupFile(path=member, size=size, sha256=digest))
    return copied


def _pack(index: BackupIndex, root: Path, out: Path) -> None:
    """Write the index and every member into a gzip'd tar, then rename it to ``out``."""
    part = out.with_name(f".{out.name}.part")
    try:
        with tarfile.open(part, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            # The index first, so a reader learns what to expect before any member arrives.
            body = index.model_dump_json().encode("utf-8")
            tar.addfile(_info(INDEX_MEMBER, len(body)), io.BytesIO(body))
            for entry in index.files:
                with (root / entry.path).open("rb") as stream:
                    tar.addfile(_info(entry.path, entry.size), stream)
        os.chmod(part, ARCHIVE_MODE)
        os.replace(part, out)
    finally:
        # Only still there when packing failed; a half archive never takes the real name.
        part.unlink(missing_ok=True)


def _info(name: str, size: int) -> tarfile.TarInfo:
    """A plain, owner-only regular-file header carrying no local user, group or time."""
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = ARCHIVE_MODE
    return info
