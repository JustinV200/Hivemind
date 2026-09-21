"""Provide `hive cells leavings list|remove`: read and clear a Cell's Leavings ledger rows.

`hive cells leavings list CELL --manifest hive.toml [--include-removed] [--json]` prints every
Leaving `hivemind.cell.local.HiveStandLeaseReleaser.release` recorded for `CELL` (active rows only,
unless `--include-removed`): path, sha256, size, task, lease, who approved it, the reason and its
left/removed times -- never `Leaving.prior`, the bytes `remove` replays (codingrules section 12:
never log full page contents). `hive cells leavings remove CELL --manifest hive.toml` replays every
active row's `prior` bytes (or unlinks, when `prior` is None) back onto the Hive Stand's real
filesystem, then marks each row removed and records one `cell.leaving_removed` event per row, all
in the order `list_leavings` returns them; a row already removed by an earlier run is simply absent
from that list, so a second `remove` is a no-op rather than an error (roadmap step 5.0a).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Nested under `hive cells` by
    `hivemind.cli.readback.cells` (`app.add_typer(leavings_app, name="leavings")`). Calls into
    `hivemind.cell` (`CellIdentity`), `hivemind.cell.leavings`, `hivemind.cli.stores` and
    `hivemind.pheromone` (`CellEvent`) only.

Key invariants:
    - `remove` replays a row's bytes *before* marking it removed (mirrors
      `hivemind.supervision.capping.apply`'s own "record before write" ordering): a crash between
      the two leaves a retryable state, the file already restored and the row still reading as
      active, never the reverse.
    - A replay failure (a locked or now-unwritable path) stops `remove` for that row only; rows
      already replayed and marked in the same invocation stay marked, and the failing row is
      reported by name rather than a bare traceback.

See Also:
    - .claude/roadmap.md step 5.0a for "hive cells leavings list|remove <cell>".
    - hivemind.cell.leavings for Leaving, LeavingsStore, ApprovedBy.
    - hivemind.cell.local.releaser for HiveStandLeaseReleaser, this ledger's one writer.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import CellIdentity
from hivemind.cell.leavings import Leaving, LeavingsStore
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    JsonOption,
    ManifestOption,
    load_manifest_or_exit,
    open_leavings,
)
from hivemind.manifest import HiveManifest
from hivemind.pheromone import CellEvent
from waggle.clock import SystemClock
from waggle.ids import CellId, new_event_id

app = typer.Typer(name="leavings", help="List and remove a Cell's Leavings ledger rows.")

__all__ = ["app"]

_IncludeRemovedOption = Annotated[
    bool, typer.Option("--include-removed", help="Also list rows already removed.")
]


class _LeavingRow(BaseModel):
    """One `hive cells leavings list` row: never `Leaving.prior` (module docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(description="The path left on the Cell.")
    sha256: str = Field(description="sha256 of the content left at `path`.")
    size: int = Field(description="Size, in bytes, of the content left at `path`.")
    task_id: str | None = Field(description="The task whose lease wrote this path.")
    lease_id: str = Field(description="The lease that released with this path left.")
    approved_by: str = Field(description="POLICY or HUMAN.")
    reason: str = Field(description="Why this path was allowed to stay.")
    left_at: str = Field(description="When release() recorded this row, ISO 8601.")
    removed_at: str | None = Field(description="When `remove` marked this row, ISO 8601, or None.")


@app.command("list")
def list_command(
    cell: Annotated[str, typer.Argument(help="The Cell id to list Leavings for.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
    include_removed: _IncludeRemovedOption = False,
    as_json: JsonOption = False,
) -> None:
    """List CELL's Leavings: active rows only, unless --include-removed."""
    loaded = load_manifest_or_exit(manifest)
    store = open_leavings(_db(loaded))
    leavings = asyncio.run(store.list_leavings(CellId(cell), include_removed=include_removed))
    rows = tuple(_leaving_row(leaving) for leaving in leavings)
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_table(rows)


@app.command("remove")
def remove_command(
    cell: Annotated[str, typer.Argument(help="The Cell id to remove every active Leaving from.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Replay (or unlink) and mark removed every active Leaving on CELL."""
    loaded = load_manifest_or_exit(manifest)
    store = open_leavings(_db(loaded))
    identity = CellIdentity(hive_id=loaded.hive.id, node_id=loaded.hive.node_id, actor="human")
    try:
        # SAFETY: top of a CLI command's own input-loading step (codingrules section 10): a
        # locked or now-unwritable path becomes one clean stderr line, not a raw traceback.
        removed = asyncio.run(_remove_all(store, CellId(cell), identity))
    except OSError as exc:
        typer.echo(f"could not replay a Leaving: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"removed {len(removed)} leaving(s) from cell {cell}")
    for path in removed:
        typer.echo(f"  {path}")


async def _remove_all(
    store: LeavingsStore, cell_id: CellId, identity: CellIdentity
) -> tuple[Path, ...]:
    """Replay-then-mark every active Leaving on `cell_id`, in `list_leavings`'s own order."""
    clock = SystemClock()
    leavings = await store.list_leavings(cell_id)
    removed: list[Path] = []
    for leaving in leavings:
        # Blocking filesystem I/O (write or unlink); off the loop like every other Cell-touching
        # write in this package (hivemind.cell.local.releaser's own to_thread calls).
        await asyncio.to_thread(_replay_leaving, leaving)
        event = CellEvent(
            id=new_event_id(clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            at=clock.now(),
            actor=identity.actor,
            kind="cell.leaving_removed",
            subject_id=cell_id,
            payload={"lease_id": leaving.lease_id, "path": str(leaving.path)},
        )
        await store.mark_removed(cell_id, leaving.path, clock.now(), event)
        removed.append(leaving.path)
    return tuple(removed)


def _replay_leaving(leaving: Leaving) -> None:
    """Write `leaving.prior` back, or unlink when it was None.

    Raises:
        OSError: The write or unlink failed; `remove_command` turns this into a clean exit.
    """
    if leaving.prior is None:
        leaving.path.unlink(missing_ok=True)
    else:
        leaving.path.parent.mkdir(parents=True, exist_ok=True)
        leaving.path.write_bytes(leaving.prior)


def _db(manifest: HiveManifest) -> Path:
    """Resolve the Hive's SQLite file from a loaded manifest, matching every store command."""
    return manifest.resolve_path(manifest.hive.db)


def _leaving_row(leaving: Leaving) -> _LeavingRow:
    """Build one `hive cells leavings list` row from a Leaving."""
    return _LeavingRow(
        path=str(leaving.path),
        sha256=leaving.sha256,
        size=leaving.size,
        task_id=leaving.task_id,
        lease_id=leaving.lease_id,
        approved_by=leaving.approved_by.value,
        reason=leaving.reason,
        left_at=leaving.left_at.isoformat(),
        removed_at=leaving.removed_at.isoformat() if leaving.removed_at is not None else None,
    )


def _print_table(rows: tuple[_LeavingRow, ...]) -> None:
    """Print one fixed-width table row per Leaving."""
    typer.echo(
        f"{'PATH':<40}  {'SHA256':<10}  {'SIZE':>10}  {'APPROVED BY':<11}  {'LEFT AT':<26}  REMOVED"
    )
    for row in rows:
        typer.echo(
            f"{row.path:<40}  {row.sha256[:10]:<10}  {row.size:>10}  {row.approved_by:<11}  "
            f"{row.left_at:<26}  {row.removed_at or '-'}"
        )
