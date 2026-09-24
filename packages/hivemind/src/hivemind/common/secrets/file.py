"""Provide FileSecretStore: one owner-only file per secret under one owner-only directory.

The Hive's durable secret store (``hivemind.common.secrets.protocol.SecretStore``) for a Hive
Stand (the machine the Queen, the orchestrator, runs on): the directory named by the manifest's
``[hive] secrets_dir`` holds one file per secret, named exactly as the secret is. On POSIX the
directory is ``0o700`` and every file ``0o600``, set at creation so a secret is never readable by
anyone else, not even for an instant. On Windows ``os.chmod`` can only toggle the read-only flag,
so those modes cannot be expressed there; the user profile's ACL (only the account and
administrators may read it) is what protects the directory, and nothing here fails for it. Writes
are atomic: the value goes to a fresh temporary file in the same directory, is flushed to disk and
then renamed over the target with ``os.replace``, so a reader sees the old secret or the new one
and a crash never leaves half a key behind. Every blocking call runs under ``asyncio.to_thread``
(codingrules section 11).

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal). Constructed by a composition root from the
    manifest's resolved ``[hive] secrets_dir`` (``hivemind.cli.compose.hive.build_hive`` for the
    Hive's signing key); read and written through the ``SecretStore`` protocol by
    ``hivemind.common.secrets.signers`` and ``hivemind.entrance.enrol.console``.

Key invariants:
    - ``repr`` shows the root directory and nothing else; no method logs, and no error carries, a
      value (codingrules section 13).
    - A temporary file is created with mode ``0o600`` (``tempfile.mkstemp``), starts with a dot
      (so it never matches ``SECRET_NAME_PATTERN`` and never shows up in ``names``), and is
      removed whenever the rename does not happen.
    - Reads never create the directory: a store nothing was ever written to reads as empty.

See Also:
    - hivemind.common.secrets.protocol for the contract, the name grammar and the size bound.
    - hivemind.manifest.schema.core.HiveSection.secrets_dir for where the directory is configured.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from hivemind.common.secrets.protocol import (
    SECRET_NAME_PATTERN,
    check_secret_name,
    check_secret_value,
)

DIRECTORY_MODE = 0o700  # Owner may list, read and write; group and others get nothing (POSIX).
FILE_MODE = 0o600  # Owner may read and write the secret; group and others get nothing (POSIX).

__all__ = ["DIRECTORY_MODE", "FILE_MODE", "FileSecretStore"]


class FileSecretStore:
    """Hold each secret in its own owner-only file under ``root``, written atomically.

    Safe to share across coroutines: every operation is one self-contained file-system call run
    on a worker thread, and ``os.replace`` makes each write atomic, so no lock is needed.
    """

    def __init__(self, root: Path) -> None:
        """Point the store at ``root``; nothing is created until the first ``put``.

        Args:
            root: The directory that holds the secrets, normally the manifest's resolved
                ``[hive] secrets_dir``. Created (with its parents) on the first write.
        """
        self._root = root

    @property
    def root(self) -> Path:
        """The directory this store keeps its secret files in.

        Returns:
            The path given at construction; it may not exist yet.
        """
        return self._root

    def __repr__(self) -> str:
        """Identify the store by its directory only; a value is never part of a repr."""
        return f"FileSecretStore(root={str(self._root)!r})"

    async def get(self, name: str) -> bytes | None:
        """Return the secret stored under ``name``; see SecretStore.get."""
        check_secret_name(name)
        # Blocking: one small file read on a local disk; sub-millisecond.
        return await asyncio.to_thread(self._read, name)

    async def put(self, name: str, value: bytes) -> None:
        """Store ``value`` under ``name`` atomically; see SecretStore.put."""
        check_secret_name(name)
        check_secret_value(value)
        # Blocking: a temporary file, two fsyncs and a rename; a few milliseconds on a local disk.
        await asyncio.to_thread(self._write, name, value)

    async def delete(self, name: str) -> None:
        """Remove the secret stored under ``name``, if any; see SecretStore.delete."""
        check_secret_name(name)
        # Blocking: one unlink; missing_ok makes an absent secret (or directory) a no-op.
        await asyncio.to_thread((self._root / name).unlink, missing_ok=True)

    async def names(self) -> tuple[str, ...]:
        """Return every stored secret's name, sorted; see SecretStore.names."""
        # Blocking: one directory listing.
        return await asyncio.to_thread(self._list)

    def _read(self, name: str) -> bytes | None:
        """Read one secret file, or return None when it (or the whole directory) is absent."""
        try:
            return (self._root / name).read_bytes()
        except FileNotFoundError:
            return None

    def _write(self, name: str, value: bytes) -> None:
        """Write ``value`` to a temporary file beside the target, flush it, then rename it over."""
        self._ensure_root()
        # mkstemp creates the file with FILE_MODE from the start and in the same directory, which
        # is what makes the rename below atomic (a rename never crosses file systems here).
        handle, temporary = tempfile.mkstemp(dir=self._root, prefix=f".{name}.", suffix=".tmp")
        renamed = False
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(value)
                stream.flush()
                # The bytes must be on disk before the rename publishes them, or a crash could
                # leave the name pointing at an empty file.
                os.fsync(stream.fileno())
            os.chmod(temporary, FILE_MODE)
            os.replace(temporary, self._root / name)
            renamed = True
        finally:
            # A failed write must not leave a partial secret lying around under a dotfile name.
            if not renamed:
                Path(temporary).unlink(missing_ok=True)
        _fsync_directory(self._root)

    def _ensure_root(self) -> None:
        """Create the root directory if needed and make it owner-only, whoever created it."""
        self._root.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
        # mkdir's mode is filtered by the umask and ignored for a directory that already exists
        # (an operator may have made it by hand with 0o755), so the mode is set explicitly.
        os.chmod(self._root, DIRECTORY_MODE)

    def _list(self) -> tuple[str, ...]:
        """List the regular files under the root whose names are valid secret names."""
        # A store nothing was ever written to has no directory yet: it simply holds no secrets.
        if not self._root.is_dir():
            return ()
        # Temporary files (dot-prefixed) and anything a human dropped in with another name are
        # not secrets this store wrote, so they are left out rather than reported.
        return tuple(
            sorted(
                entry.name
                for entry in self._root.iterdir()
                if entry.is_file() and SECRET_NAME_PATTERN.fullmatch(entry.name) is not None
            )
        )


def _fsync_directory(directory: Path) -> None:
    """Flush a rename inside ``directory`` to disk, where the platform has a way to."""
    # POSIX makes a rename durable only once its directory is synced; os.O_DIRECTORY exists only
    # there. Windows offers no directory handle to flush and makes MoveFileEx durable itself.
    if not hasattr(os, "O_DIRECTORY"):
        return
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
