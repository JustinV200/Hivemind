"""Open a Hive backup archive, trusting nothing in it until every member checks out.

A backup arrives from somewhere else (another disk, another machine, Supersedure's stream), so it
is read as hostile input. The whole file's SHA-256 is compared with the one the operator expects,
when given. The first member must be the index, small and valid. Every member after it must be a
regular file (never a link, device or directory) that the index lists, exactly once, at the size
it lists; its bytes are streamed to a path this module builds from the validated name (tar's own
`extract` is never called, so no name, link or mode in the archive decides where a byte lands),
and hashed on the way. Anything missing, extra, resized or with a wrong digest refuses the whole
archive. Last, the database snapshot must pass SQLite's own `PRAGMA integrity_check`.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Called by `hive restore`
    (`hivemind.cli.backup`) before `hivemind.common.backup.restore` touches the destination.

Key invariants:
    - Nothing outside ``workdir`` is ever written, whatever the archive holds.
    - An `OpenedBackup` exists only for an archive whose every member matched its index and
      whose database passed the integrity check.

See Also:
    - hivemind.common.backup.index for the name rules every member is held to.
    - docs/adr/0064-backups-are-one-checksummed-archive-without-secrets.md.
"""

from __future__ import annotations

import gzip
import sqlite3
import tarfile
import zlib
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from hivemind.common.backup.digest import copy_hashed, sha256_file
from hivemind.common.backup.index import DB_MEMBER, INDEX_MEMBER, MAX_INDEX_BYTES, BackupIndex
from hivemind.common.errors import BackupError

__all__ = ["OpenedBackup", "open_backup"]


@dataclass(frozen=True, slots=True)
class OpenedBackup:
    """An archive whose members all matched its index, extracted under ``root``.

    Attributes:
        index: The archive's `backup.json`.
        sha256: The archive file's SHA-256.
        root: The extraction directory: `hive.sqlite3` and one directory per tree.
    """

    index: BackupIndex
    sha256: str
    root: Path

    @property
    def db(self) -> Path:
        """The extracted database snapshot."""
        return self.root / DB_MEMBER


def open_backup(archive: Path, workdir: Path, expect_sha256: str | None = None) -> OpenedBackup:
    """Check an archive end to end and extract it under ``workdir``.

    Blocking (disk); an async caller runs it under ``asyncio.to_thread``.

    Args:
        archive: The backup file.
        workdir: An empty directory the members are extracted into.
        expect_sha256: The archive's SHA-256 as the operator recorded it at backup time; None
            skips that one comparison (every member is still checked against the index).

    Returns:
        The opened backup.

    Raises:
        BackupError: The archive's checksum, layout, any member or the database is not what the
            index says, or the file is not a backup archive at all.
        OSError: The archive cannot be read or ``workdir`` written.
    """
    digest = sha256_file(archive)
    if expect_sha256 is not None and expect_sha256.strip().lower() != digest:
        raise BackupError(f"{archive} has SHA-256 {digest}, not the one expected.")
    try:
        with tarfile.open(archive, "r:gz") as tar:
            index = _read_index(tar)
            _extract_members(tar, index, workdir)
    except (tarfile.TarError, EOFError, zlib.error, gzip.BadGzipFile) as exc:
        # A truncated or foreign file: the archive itself, not one member, is unreadable.
        raise BackupError(f"{archive} is not a readable backup archive.") from exc
    _check_integrity(workdir / DB_MEMBER)
    return OpenedBackup(index, digest, workdir)


def _read_index(tar: tarfile.TarFile) -> BackupIndex:
    """Read and validate the first member, which must be a small `backup.json`."""
    member = tar.next()
    if member is None or member.name != INDEX_MEMBER or not member.isreg():
        raise BackupError(f"The archive does not start with {INDEX_MEMBER}.")
    # Bounded before it is read: a huge index is a hostile one, not a big Hive.
    if member.size > MAX_INDEX_BYTES:
        raise BackupError(f"{INDEX_MEMBER} is larger than {MAX_INDEX_BYTES} bytes.")
    stream = tar.extractfile(member)
    if stream is None:
        raise BackupError(f"{INDEX_MEMBER} could not be read.")
    try:
        return BackupIndex.model_validate_json(stream.read())
    except ValidationError as exc:
        # The field names say what is wrong; the archive's own text is never echoed.
        fields = ", ".join(str(error["loc"]) for error in exc.errors())
        raise BackupError(f"{INDEX_MEMBER} is not a valid index ({fields}).") from exc


def _extract_members(tar: tarfile.TarFile, index: BackupIndex, workdir: Path) -> None:
    """Stream every member after the index to its own path, checking it against the index."""
    expected = {entry.path: entry for entry in index.files}
    seen: set[str] = set()
    # Each member must be one the index lists, not seen before, a plain file of the listed size.
    while (member := tar.next()) is not None:
        entry = expected.get(member.name)
        if entry is None or member.name in seen or not member.isreg():
            raise BackupError(f"Backup member {member.name!r} is not one its index lists.")
        if member.size != entry.size:
            raise BackupError(f"Backup member {member.name!r} is not the size its index lists.")
        stream = tar.extractfile(member)
        if stream is None:
            raise BackupError(f"Backup member {member.name!r} could not be read.")
        # The target is built from the index's validated name, never from the tar header.
        digest, _ = copy_hashed(stream, workdir / entry.path)
        if digest != entry.sha256:
            raise BackupError(f"Backup member {member.name!r} does not match its checksum.")
        seen.add(member.name)
    missing = sorted(set(expected) - seen)
    if missing:
        raise BackupError(f"The archive is missing {len(missing)} member(s), e.g. {missing[0]!r}.")


def _check_integrity(db: Path) -> None:
    """Refuse a snapshot SQLite itself says is damaged."""
    with closing(sqlite3.connect(str(db))) as connection:
        try:
            # Latency: one read of every page; seconds for a large Hive.
            verdict = connection.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"The backup's {DB_MEMBER} is not a SQLite database.") from exc
    if verdict != "ok":
        raise BackupError(f"The backup's {DB_MEMBER} failed SQLite's integrity check.")
