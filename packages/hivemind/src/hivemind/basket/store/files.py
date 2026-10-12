"""Keep blob bytes in a directory tree named by hash, written atomically and read verified.

The Basket's metadata lives in the Hive's SQLite database; the bytes live here, in
`<root>/<sha256[:2]>/<sha256>`, the Comb's own pattern: two hex digits of fan-out keep a directory
small, and the hash as the file name makes the tree self-describing and self-checking. A write goes
to a hidden `.<sha256>.part` file in the same directory and is moved into place with `os.replace`,
so a crash leaves either the whole file or an orphan `.part` that the next write overwrites, never a
half-written blob under its real name. A read hashes the bytes again before returning them: a bit
rotted or tampered file is an integrity error, not data.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store`. Called
    by `SqliteBasketStore` from its connection thread; every method is synchronous and blocking
    (disk), so callers run it off the event loop. Calls into `hivemind.basket.errors` only.

Key invariants:
    - A file at `path_for(sha)` always holds bytes hashing to `sha`: `write` never leaves a
      partial file there and `read` re-checks.
    - A hash that is not 64 lowercase hex digits never reaches the filesystem, so a hostile hash
      can never escape `root` (path traversal).
    - Nothing here is ever executed: bytes are read and returned, never run or imported.

See Also:
    - hivemind.basket.store.sqlite for the caller that pairs these files with their rows.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the byte-directory layout.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from hivemind.basket.errors import BlobIntegrityError, BlobNotFoundError

FAN_OUT_CHARS = 2  # Leading hex digits that name the subdirectory (256 directories of blobs).
FILE_MODE = 0o600  # Blob files are the Hive's data: owner only.
DIR_MODE = 0o700  # So are the directories.
_SHA256 = re.compile(r"[0-9a-f]{64}")  # The one digest form (waggle SHA256_PATTERN).

__all__ = ["BlobFiles"]


class BlobFiles:
    """The byte directory under one root: write once, read verified."""

    def __init__(self, root: Path) -> None:
        """Remember the root; nothing is created until the first write.

        Args:
            root: The directory holding the `<sha[:2]>/<sha>` tree; created on first write.
        """
        self._root = root

    def path_for(self, sha256: str) -> Path:
        """Return where a blob's bytes live.

        Args:
            sha256: The blob's hash.

        Returns:
            `<root>/<sha256[:2]>/<sha256>`.

        Raises:
            BlobNotFoundError: `sha256` is not a lowercase hex SHA-256 digest.
        """
        # Validated before it becomes a path: a hash like "../x" must never leave the root.
        if _SHA256.fullmatch(sha256) is None:
            raise BlobNotFoundError(sha256)
        return self._root / sha256[:FAN_OUT_CHARS] / sha256

    def exists(self, sha256: str) -> bool:
        """Return whether a file is present for this hash (not whether it still verifies)."""
        return self.path_for(sha256).is_file()

    def write(self, sha256: str, data: bytes) -> bool:
        """Store `data` under `sha256` unless a verified copy is already there.

        Args:
            sha256: The hash `data` must have; the caller has already checked it.
            data: The bytes to keep.

        Returns:
            True when this call wrote the file, False when a copy that still verifies was
            already present (a dedupe: the file is not rewritten).
        """
        final = self.path_for(sha256)
        # A present copy that still hashes right is the dedupe case; a corrupt one is overwritten.
        if final.is_file() and _digest(final.read_bytes()) == sha256:
            return False
        final.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        part = final.with_name(f".{sha256}.part")
        # Written whole to a hidden name, flushed to disk, then renamed: the real name appears
        # only when the bytes are complete.
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, final)
        return True

    def read(self, sha256: str) -> bytes:
        """Return the bytes stored for `sha256`, after hashing them again.

        Args:
            sha256: The blob's hash.

        Returns:
            Bytes whose SHA-256 equals `sha256`.

        Raises:
            BlobNotFoundError: `sha256` is not a digest.
            BlobIntegrityError: The file is missing or its bytes no longer match the hash.
        """
        path = self.path_for(sha256)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise BlobIntegrityError(sha256, "its file is missing from the byte directory") from exc
        # The check that makes the byte directory trustworthy: disk rot or tampering is caught
        # here, before a reader ever sees the bytes.
        if _digest(data) != sha256:
            raise BlobIntegrityError(sha256, "its bytes no longer hash to its name")
        return data


def _digest(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of `data`."""
    return hashlib.sha256(data).hexdigest()
