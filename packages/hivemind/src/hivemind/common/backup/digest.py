"""Hash files and copy streams while hashing them, in bounded chunks, for the backup archive.

Every byte a backup writes or reads back is counted and hashed (SHA-256) on the way through, so
a checksum always describes exactly the bytes that were copied, never a file that may have
changed between a hash and a copy. Reads go in fixed chunks so a large database or blob never
has to fit in memory. A copy lands in a hidden `.part` file beside its target and is renamed over
it only when complete, so a crash never leaves half a file under a real name.

Fits into the Hive:
    Layer 0 (primitives; standard library only). Used by `hivemind.common.backup.write`,
    `.read` and `.restore`.

Key invariants:
    - `copy_hashed` publishes its target only after every byte is written and flushed; on any
      failure the `.part` file is removed and the target is untouched.

See Also:
    - hivemind.common.backup.index for the digests these produce.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import IO

CHUNK_BYTES = 1024 * 1024  # 1 MiB: large enough to stream fast, small enough to never matter.
FILE_MODE = 0o600  # What a backup writes is the Hive's data: owner only (POSIX).
# O_BINARY exists only on Windows, where without it the C runtime would rewrite newlines.
_OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)

__all__ = ["CHUNK_BYTES", "copy_hashed", "sha256_file"]


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file's bytes, lowercase hex.

    Args:
        path: The file to hash.

    Returns:
        Its digest.

    Raises:
        OSError: The file cannot be read.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        # Chunked so a multi-gigabyte database is hashed in constant memory.
        while chunk := stream.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def copy_hashed(source: IO[bytes], target: Path) -> tuple[str, int]:
    """Copy a stream to ``target`` atomically, returning the SHA-256 and length of what was copied.

    Args:
        source: An open binary stream, read to its end.
        target: Where the bytes land; its parent is created if needed.

    Returns:
        The copied bytes' digest (lowercase hex) and their count.

    Raises:
        OSError: The target cannot be written.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(f".{target.name}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        # Created with FILE_MODE from the start; a stale .part from a crash is just overwritten.
        with os.fdopen(os.open(part, _OPEN_FLAGS, FILE_MODE), "wb") as out:
            while chunk := source.read(CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(part, target)
    finally:
        # Only reached with the .part still there when something above failed.
        part.unlink(missing_ok=True)
    return digest.hexdigest(), size
