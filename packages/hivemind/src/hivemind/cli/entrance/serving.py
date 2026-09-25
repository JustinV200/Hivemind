"""Keep one ``hive serve`` per Hive, and tell the Hive Stand's other commands where it listens.

Two facts about a running ``hive serve`` matter to every other command on the Hive Stand (the
machine the Queen, the orchestrator, runs on). The console commands (``hive entrance invite``,
``approve``, ...) act as the console device over its loopback listener, so they need that
listener's port, which the manifest may leave to the operating system (``bind = "127.0.0.1:0"``).
The offline operations (``hive entrance operator password --reset``, ``unlock --console``) change
what a running Entrance holds in memory, so they must know it is not running, and it must not
start while they work. A port that answers is no proof of either (another program may hold it,
and a crash leaves no trace), so the proof is a lock: the **serve lock**, an exclusive lock the
operating system holds on ``<db>.serve.lock`` for as long as its holder's process has it open
(``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows), released by the kernel even when the
holder is killed. ``hive serve`` holds it for its whole life and publishes the **serve record**
(``<db>.serve.json``: its pid and its loopback listener's host and port) once the listener serves;
an offline operation holds the same lock for its duration, so neither can run beside the other.
The lock file also names its holder, for the refusal a second holder gets.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Used by ``hivemind.cli.compose.
    entrance.serve_hive`` (hold, publish) and by the console and offline commands of
    ``hive entrance`` (read, hold). Calls into the standard library and pydantic only.

Key invariants:
    - At most one process holds a Hive's serve lock; the kernel releases it with the process.
    - The serve record is written atomically and only while its writer holds the serve lock; a
      record whose writer has gone is never trusted without the lock saying someone serves.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md: "--reset works only
      while hive serve is stopped", and the console as a device on loopback.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import IO, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hivemind.common.errors import ConflictError
from hivemind.common.logging import get_logger

LOCK_SUFFIX = ".serve.lock"  # Beside the Hive's database: one lock per Hive, whatever the manifest.
RECORD_SUFFIX = ".serve.json"  # The running serve's pid and loopback listener.
SERVE_HOLDER = "hive serve"  # What a running serve's lock file says.
# Windows locks bytes, and a locked byte cannot be read by anyone else: the lock sits far past the
# holder's name, so a refused process can still read who holds it.
_WINDOWS_LOCK_OFFSET = 1 << 20
_MAX_HOLDER_CHARS = 200  # The holder's line in the lock file: a pid and a command name.

log = get_logger(__name__)

__all__ = [
    "LOCK_SUFFIX",
    "RECORD_SUFFIX",
    "SERVE_HOLDER",
    "HiveBusyError",
    "ServeRecord",
    "hold_serve_lock",
    "publish_serve_record",
    "read_serve_record",
]


class HiveBusyError(ConflictError):
    """Raise when another process holds the serve lock: ``hive serve``, or an offline step."""

    code: ClassVar[str] = "hivemind.cli.hive_busy"


class ServeRecord(BaseModel):
    """Where a running ``hive serve`` answers, as it published it once its listener served.

    Written by ``hive serve`` and read by the console commands on the same Hive Stand; a local
    file, never a network boundary, validated like one all the same.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pid: int = Field(ge=1, description="The serving process.")
    host: str = Field(min_length=1, description="The loopback listener's host, as bound.")
    port: int = Field(ge=1, le=65_535, description="The loopback listener's port, as bound.")
    started_at: datetime = Field(description="When the listener started serving.")

    @property
    def origin(self) -> str:
        """The loopback listener's origin, an IPv6 host in brackets."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"


@contextmanager
def hold_serve_lock(db: Path, holder: str) -> Iterator[None]:
    """Hold the Hive's serve lock for the block, or refuse at once if another process holds it.

    Args:
        db: The Hive's database file (``[hive] db``, resolved); the lock sits beside it.
        holder: What this process is doing, for another process's refusal (``"hive serve"``).

    Yields:
        Nothing; the lock is held until the block exits (or the process ends).

    Raises:
        HiveBusyError: Another process holds it; the message names what it said it was doing.
    """
    path = db.with_name(db.name + LOCK_SUFFIX)
    # "a+" creates the file when missing and never truncates what another holder wrote.
    with path.open("a+", encoding="utf-8") as handle:
        if not _try_lock(handle):
            raise HiveBusyError(
                f"The Hive at {db} is in use by another process ({_holder(path)}); stop it first."
            )
        try:
            _name_holder(handle, holder)
            yield
        finally:
            _unlock(handle)


@contextmanager
def publish_serve_record(db: Path, record: ServeRecord) -> Iterator[None]:
    """Publish where ``hive serve`` listens for the block; remove it on the way out.

    Args:
        db: The Hive's database file; the record sits beside it.
        record: The pid and the loopback listener, as bound.

    Yields:
        Nothing; the record is on disk until the block exits.
    """
    path = db.with_name(db.name + RECORD_SUFFIX)
    # Atomic: a reader sees the old file, no file, or the whole new one, never half of one.
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(record.model_dump_json())
    os.replace(temporary, path)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def read_serve_record(db: Path) -> ServeRecord | None:
    """Read where a running ``hive serve`` listens, if one published it.

    Args:
        db: The Hive's database file.

    Returns:
        The record, or None when there is none or it cannot be read (a serve still starting, or
        one that crashed before removing it: the caller's connection then finds no listener).
    """
    path = db.with_name(db.name + RECORD_SUFFIX)
    try:
        return ServeRecord.model_validate_json(path.read_bytes())
    except (FileNotFoundError, ValidationError):
        return None


def _try_lock(handle: IO[str]) -> bool:
    """Take the exclusive lock without waiting; False when another process holds it."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(_WINDOWS_LOCK_OFFSET)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    else:
        # An explicit branch, so mypy checks each platform's half on its own platform only.
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def _unlock(handle: IO[str]) -> None:
    """Release the lock early; closing the file (or the process ending) releases it anyway."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(_WINDOWS_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _name_holder(handle: IO[str], holder: str) -> None:
    """Write who holds the lock into the lock file, for a refused process to read."""
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid {os.getpid()}: {holder}"[:_MAX_HOLDER_CHARS])
    handle.flush()


def _holder(path: Path) -> str:
    """Read what the lock's holder wrote; a holder that has not written yet is just "running"."""
    try:
        text = path.read_text(encoding="utf-8")[:_MAX_HOLDER_CHARS].strip()
    except OSError as exc:
        # The name only decorates the refusal; the refusal itself stands without it.
        log.debug("serve_lock.holder_unreadable", error=type(exc).__name__)
        return "running"
    return text or "running"
