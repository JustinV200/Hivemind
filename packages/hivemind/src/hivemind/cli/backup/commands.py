"""Run `hive backup OUT` and `hive restore ARCHIVE`: the terminal side of roadmap 13.7.

`hive backup` writes one checksummed archive of the Hive's stores while the Hive may keep running
(SQLite's online backup API reads a consistent snapshot beside the Queen's writes) and prints the
archive's SHA-256 for the operator to keep with it. `hive restore` puts one back. It holds the
Hive's serve lock and Queen lock for its whole run, so no `hive serve` or `hive run` can have the
database open while its pages are replaced, refuses to replace an existing database without
`--replace`, checks the archive end to end (and against `--sha256` when given), checks that the
destination is the same Hive with the same secrets, and only then writes. Both commands print one
refusal line and exit 1 on a typed failure.

Fits into the Hive:
    Layer 7 (the terminal), inside `hivemind.cli.backup`. Registered on the root `hive`
    application by `hivemind.cli.app`. Calls into `hivemind.common.backup`, the package's `hive`
    module, `hivemind.cli.entrance` (the two locks) and `hivemind.cli.stores`.

Key invariants:
    - `hive restore` writes nothing until the archive, the Hive id and the secret store all
      checked out, and never while another process holds either lock.
    - The extraction directory sits beside the database and is removed however the command ends.

See Also:
    - docs/runbooks/backup.md for the operator's walk-through.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from hivemind.cli.backup.hive import (
    BASKET_TREE,
    backup_source,
    check_destination,
    record_restored,
)
from hivemind.cli.entrance import hold_queen_lock, hold_serve_lock
from hivemind.cli.landing import describe
from hivemind.cli.stores import DEFAULT_MANIFEST, ManifestOption, load_manifest_or_exit
from hivemind.common.backup import open_backup, restore_backup, write_backup
from hivemind.common.errors import BackupError, HiveMindError
from hivemind.manifest import HiveManifest
from waggle.clock import SystemClock

RESTORE_HOLDER = "hive restore"  # What another process's refusal says holds the locks.

__all__ = ["backup_command", "restore_command"]


def backup_command(
    out: Annotated[Path, typer.Argument(help="The archive to write, e.g. hive-2026-10-27.tar.gz.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Write one checksummed archive of the Hive's stores; the secret store is never in it."""
    loaded = load_manifest_or_exit(manifest)
    receipt = _run(
        "backup", lambda: write_backup(asyncio.run(backup_source(loaded)), out, SystemClock())
    )
    typer.echo(f"Backed up Hive {receipt.index.hive_id} to {receipt.path}")
    typer.echo(f"  sha256: {receipt.sha256}  (keep it; hive restore --sha256 checks it)")
    typer.echo(f"  files: {len(receipt.index.files)}, bytes: {receipt.size}")
    typer.echo("The secret store is not in the backup; keep it safe on its own.")


def restore_command(
    archive: Annotated[Path, typer.Argument(help="A backup written by hive backup.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    sha256: Annotated[
        str | None,
        typer.Option("--sha256", help="The archive's SHA-256, as hive backup printed it."),
    ] = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Replace the Hive's existing database.")
    ] = False,
) -> None:
    """Put a backup back over the Hive's stores, after checking every member of it."""
    loaded = load_manifest_or_exit(manifest)
    written = _run("restore", lambda: _restore(loaded, archive, sha256, replace=replace))
    typer.echo(f"Restored Hive {loaded.hive.id} from {archive} ({written} Basket files written).")
    typer.echo("Start the Hive as usual; its first start is a Requeening from the restored stores.")


def _restore(manifest: HiveManifest, archive: Path, sha256: str | None, *, replace: bool) -> int:
    """Check and restore under both locks; return how many Basket files were written."""
    db = manifest.resolve_path(manifest.hive.db)
    # The locks live beside the database, so its directory must exist before they are taken.
    db.parent.mkdir(parents=True, exist_ok=True)
    with hold_serve_lock(db, RESTORE_HOLDER), hold_queen_lock(db, RESTORE_HOLDER):
        if db.exists() and not replace:
            raise BackupError(f"{db} already holds a Hive; pass --replace to restore over it.")
        with tempfile.TemporaryDirectory(dir=db.parent, prefix=".hive-restore-") as work:
            opened = open_backup(archive, Path(work), sha256)
            asyncio.run(check_destination(opened.index, manifest))
            written = restore_backup(opened, db, {BASKET_TREE: manifest.basket_root()})
            record_restored(manifest, opened, written, SystemClock())
    return written


def _run[T](verb: str, work: Callable[[], T]) -> T:
    """Run the work; a typed refusal is one stderr line and exit 1."""
    try:
        return work()
    except (HiveMindError, OSError) as exc:
        # Typed failures only: a refused archive or destination, a held lock, or the disk.
        _refuse(verb, exc)


def _refuse(verb: str, exc: Exception) -> NoReturn:
    """Print why `hive <verb>` could not act, and exit 1."""
    typer.echo(f"hive {verb} refused: {describe(exc)}", err=True)
    raise typer.Exit(code=1) from exc
