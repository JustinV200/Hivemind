"""Keep an incoming blob's bytes in a temp file while it arrives, hash it, and hand it over.

The receiver never holds a whole blob in memory: each chunk is appended to a `<sha256>.part` file
in a spool directory, the whole file is hashed in blocks when the last chunk lands, and on success
the file is renamed to `<sha256>` for the caller to hand to the store. The partial file also is
what makes a transfer survive a restart: a new receiver for the same offer finds the file and
resumes from its length. Every function is synchronous and blocking (disk); the receiver calls them
through `asyncio.to_thread`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.transfer`.
    Called by `receiver` only. Calls into the standard library only.

Key invariants:
    - A file named `<sha256>` in the spool directory has been verified: it only appears through
      `finalize`, after the hash matched.
    - A failed verification deletes the partial file, so corrupt bytes are never resumed from.
    - The spool directory and its files are owner-only.

See Also:
    - hivemind.basket.transfer.receiver for the state machine that drives this.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

PART_SUFFIX = ".part"  # Marks a file still being written; the verified file has no suffix.
FILE_MODE = 0o600  # Owner only: a spooled blob is the Hive's data.
DIR_MODE = 0o700  # So is the spool directory.
HASH_BLOCK_BYTES = 1 << 20  # 1 MiB blocks: hashing never loads the whole file.

__all__ = ["Spool"]


class Spool:
    """One blob's spool file: append, measure, verify, finalise or discard."""

    def __init__(self, directory: Path, sha256: str) -> None:
        """Name the spool file for a blob; nothing is created yet.

        Args:
            directory: The spool directory.
            sha256: The blob's hash, already validated (it names the file).
        """
        self._part = directory / f"{sha256}{PART_SUFFIX}"
        self._final = directory / sha256
        self._sha256 = sha256

    @property
    def final_path(self) -> Path:
        """Where the verified bytes will be, once `finalize` has run."""
        return self._final

    def size(self) -> int:
        """Return how many bytes the partial file holds (0 when there is none)."""
        try:
            return self._part.stat().st_size
        except FileNotFoundError:
            return 0

    def append(self, data: bytes) -> None:
        """Append `data` to the partial file, creating the directory and file on first use."""
        self._part.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        fd = os.open(self._part, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
        with os.fdopen(fd, "ab") as handle:
            handle.write(data)

    def digest(self) -> str:
        """Return the SHA-256 of the partial file, read in blocks."""
        hasher = hashlib.sha256()
        with self._part.open("rb") as handle:
            # Stream the file so a large blob never sits in memory whole.
            while block := handle.read(HASH_BLOCK_BYTES):
                hasher.update(block)
        return hasher.hexdigest()

    def finalize(self) -> Path:
        """Rename the verified partial file to its final name and return the path."""
        os.replace(self._part, self._final)
        return self._final

    def discard(self) -> None:
        """Delete the partial file; a file that is not there is fine."""
        self._part.unlink(missing_ok=True)
