"""Copy the frozen Queen's stores onto the candidate and start her there (ADR-0065).

By the time this runs the Queen is SUPERSEDING and `queen.supersedure_started` is on her trail, so
the copy carries it. One `hive backup` archive (ADR-0064: every store, the Entrance tables and
enrolled devices, the Basket's blobs, no secret) is written beside the Hive Stand, put on the
candidate over its session, and restored there with `hive restore --sha256`, which checks the
archive's digest and every member again before it writes anything. Then the candidate's Queen is
started from the copy; she comes up REQUEENING and held. The Hive keypair never travels: the
candidate's preflight already found it in its own secret store.

Fits into the Hive:
    Layer 6 (the kernel), inside `hivemind.queen.supersedure`. Called by `.run`. Calls into
    `hivemind.common.backup`, `hivemind.queen.errors`, `.ports` and waggle only.

Key invariants:
    - Any failure is one `SupersedureFailedError` naming its step; the caller rolls back.
    - The local archive is removed once the candidate holds it or the copy failed.

See Also:
    - docs/adr/0064-backups-are-one-checksummed-archive-without-secrets.md.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from hivemind.common.backup import BackupSource, write_backup
from hivemind.common.errors import BackupError
from hivemind.queen.errors import SupersedureFailedError
from hivemind.queen.supersedure.ports import CandidateLink
from waggle.clock import Clock

ARCHIVE_NAME = "supersedure.hive.tgz"  # One Supersedure at a time, so one name.

__all__ = ["ARCHIVE_NAME", "copy_stores"]


async def copy_stores(
    source: BackupSource, link: CandidateLink, workdir: Path, clock: Clock
) -> str:
    """Back the Hive up, move the archive, restore it on the candidate and start her Queen.

    Args:
        source: What the backup copies.
        link: The candidate.
        workdir: Where the archive is written first.
        clock: Stamps the archive's index.

    Returns:
        The archive's SHA-256, as the candidate checked it.

    Raises:
        SupersedureFailedError: The backup, the transfer, the restore or the start failed.
    """
    archive = await asyncio.to_thread(_fresh_archive_path, workdir)
    try:
        # Latency: the online backup API reads the whole database once; seconds for a large Hive.
        receipt = await asyncio.to_thread(write_backup, source, archive, clock)
    except (BackupError, OSError) as exc:
        raise SupersedureFailedError("copy", f"the backup failed: {exc}") from exc
    try:
        await link.send_backup(receipt.path, receipt.sha256)
    finally:
        await asyncio.to_thread(_remove, archive)
    await link.restore(receipt.sha256)
    await link.start_queen()
    return receipt.sha256


def _fresh_archive_path(workdir: Path) -> Path:
    """The archive's path, its directory made and a failed attempt's leftover removed."""
    workdir.mkdir(parents=True, exist_ok=True)
    archive = workdir / ARCHIVE_NAME
    archive.unlink(missing_ok=True)  # `write_backup` never overwrites.
    return archive


def _remove(archive: Path) -> None:
    """Remove the local archive; a file the OS still holds is left for the next attempt."""
    with contextlib.suppress(OSError):
        archive.unlink(missing_ok=True)
