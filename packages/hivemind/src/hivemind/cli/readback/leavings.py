"""Provide `hive cells leavings list|remove`: read and clear Leavings ledger rows.

`hive cells leavings list [CELL] --manifest hive.toml [--include-removed] [--json]` prints every
Leaving `hivemind.cell.local.HiveStandLeaseReleaser.release` recorded for `CELL` (active rows only,
unless `--include-removed`): the Cell it was left on, path, sha256, size, task, lease, who approved
it, the reason and its left/removed times -- never `Leaving.prior`, the bytes `remove` replays
(codingrules section 12: never log full page contents). CELL is optional: every `hive run` mints a
fresh Cell id for the Hive Stand, so an operator cannot always name one; omitting it lists every
active Leaving across every Cell instead (`LeavingsStore.list_all_leavings`). `hive cells leavings
remove [CELL] [--path PATH] --manifest hive.toml` either replays every active row on `CELL` (today's
behaviour, `CELL` given and `--path` omitted), or replays exactly the one active row at `--path`
(scoped to `CELL` when given, searched across every Cell otherwise -- refusing with a clear message
if more than one Cell has an active Leaving at that exact path); replaying writes `prior` bytes (or
unlinks, when `prior` is None) back onto the real filesystem, then marks the row removed and records
one `cell.leaving_removed` event per row. Calling `remove` with neither `CELL` nor `--path` is a
usage error (exit 2), never "remove everything everywhere". A row already removed by an earlier run
is simply absent from `list_leavings`/`list_all_leavings`, so re-running `remove CELL` is a no-op
rather than an error (roadmap step 5.0a; the CLI-driven-only-by-a-per-run-Cell-id defect fix, phase
5 follow-up).

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
    - `--path` with no `CELL` never guesses: more than one Cell holding an active Leaving at the
      same exact path is reported by name (every matching Cell id) and nothing is removed, rather
      than picking one arbitrarily.

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
from hivemind.cell.errors import LeavingNotFoundError
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

app = typer.Typer(name="leavings", help="List and remove Leavings ledger rows.")

__all__ = ["app"]

_IncludeRemovedOption = Annotated[
    bool, typer.Option("--include-removed", help="Also list rows already removed.")
]
_CellArgument = Annotated[
    str | None, typer.Argument(help="The Cell id to scope to; omit to span every Cell.")
]
_PathOption = Annotated[
    Path | None,
    typer.Option("--path", help="Remove exactly one active Leaving at this exact path."),
]


class _LeavingRow(BaseModel):
    """One `hive cells leavings list` row: never `Leaving.prior` (module docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell_id: str = Field(description="The Cell this path was left on.")
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
    cell: _CellArgument = None,
    manifest: ManifestOption = DEFAULT_MANIFEST,
    include_removed: _IncludeRemovedOption = False,
    as_json: JsonOption = False,
) -> None:
    """List Leavings: CELL's own rows, or every Cell's when CELL is omitted."""
    loaded = load_manifest_or_exit(manifest)
    store = open_leavings(_db(loaded))
    if cell is None:
        leavings = asyncio.run(store.list_all_leavings(include_removed=include_removed))
    else:
        leavings = asyncio.run(store.list_leavings(CellId(cell), include_removed=include_removed))
    rows = tuple(_leaving_row(leaving) for leaving in leavings)
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_table(rows)


@app.command("remove")
def remove_command(
    cell: _CellArgument = None,
    path: _PathOption = None,
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Remove Leavings: every active row on CELL, or the one active row at --path."""
    if cell is None and path is None:
        # A usage error, not "remove everything everywhere" (module docstring's own invariant).
        typer.echo(
            "remove needs CELL, --path, or both; see 'hive cells leavings remove --help'.",
            err=True,
        )
        raise typer.Exit(code=2)
    loaded = load_manifest_or_exit(manifest)
    store = open_leavings(_db(loaded))
    identity = CellIdentity(hive_id=loaded.hive.id, node_id=loaded.hive.node_id, actor="human")
    try:
        # SAFETY: top of a CLI command's own input-loading step (codingrules section 10): a
        # locked or now-unwritable path, or no matching row, becomes one clean stderr line.
        removed, location = asyncio.run(_remove(store, cell, path, identity))
    except OSError as exc:
        typer.echo(f"could not replay a Leaving: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except LeavingNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"removed {len(removed)} leaving(s){location}")
    for removed_path in removed:
        typer.echo(f"  {removed_path}")


async def _remove(
    store: LeavingsStore, cell: str | None, path: Path | None, identity: CellIdentity
) -> tuple[tuple[Path, ...], str]:
    """Dispatch to `_remove_all` or `_remove_by_path`, and build the trailing "from cell ..." text.

    Exactly one of `cell`/`path` is allowed to be None on entry (`remove_command`'s own guard), so
    this never has to guess which mode was asked for.
    """
    if path is not None:
        leaving = await _remove_by_path(store, cell, path, identity)
        return (leaving.path,), f" from cell {leaving.cell_id}"
    if cell is None:
        # Unreachable: remove_command's own guard already refused "neither CELL nor --path".
        raise typer.Exit(code=2)
    removed = await _remove_all(store, CellId(cell), identity)
    return removed, f" from cell {cell}"


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
        await store.mark_removed(
            cell_id, leaving.path, clock.now(), _removed_event(identity, leaving, clock)
        )
        removed.append(leaving.path)
    return tuple(removed)


async def _remove_by_path(
    store: LeavingsStore, cell: str | None, path: Path, identity: CellIdentity
) -> Leaving:
    """Replay-then-mark the one active Leaving at `path`, scoped to `cell` when given.

    Args:
        store: The Leavings ledger.
        cell: The Cell id to scope the lookup to, or None to search every Cell.
        path: The exact path to remove; resolved before comparing.
        identity: Stamped on the `cell.leaving_removed` event this writes.

    Returns:
        The Leaving that was removed (already marked, in the caller's store).

    Raises:
        LeavingNotFoundError: No active Leaving matches `path` (scoped to `cell`, if given).
        typer.Exit: Code 1, when `cell` is None and more than one Cell has an active Leaving at
            this exact path; the message names every matching Cell.
    """
    # ASYNC240: Path.resolve() lives in a plain, non-async helper, never inline here.
    resolved = _resolved(path)
    leaving = await _one_leaving_at_path(store, cell, resolved)
    clock = SystemClock()
    await asyncio.to_thread(_replay_leaving, leaving)
    await store.mark_removed(
        leaving.cell_id, leaving.path, clock.now(), _removed_event(identity, leaving, clock)
    )
    return leaving


async def _one_leaving_at_path(store: LeavingsStore, cell: str | None, path: Path) -> Leaving:
    """Return the one active Leaving at `path`; scoped to `cell`, or searched across every Cell."""
    if cell is not None:
        return await store.get_leaving(CellId(cell), path)
    matches = tuple(leaving for leaving in await store.list_all_leavings() if leaving.path == path)
    if not matches:
        typer.echo(f"no active Leaving at {path}", err=True)
        raise typer.Exit(code=1)
    if len(matches) > 1:
        cells = ", ".join(sorted(str(leaving.cell_id) for leaving in matches))
        typer.echo(
            f"active Leaving at {path} exists on more than one Cell ({cells}); pass CELL to "
            "pick one.",
            err=True,
        )
        raise typer.Exit(code=1)
    return matches[0]


def _removed_event(identity: CellIdentity, leaving: Leaving, clock: SystemClock) -> CellEvent:
    """Build the `cell.leaving_removed` event `_remove_all`/`_remove_by_path` both record."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor=identity.actor,
        kind="cell.leaving_removed",
        subject_id=leaving.cell_id,
        payload={"lease_id": leaving.lease_id, "path": str(leaving.path)},
    )


def _resolved(path: Path) -> Path:
    """Resolve `path`, off the async call site (ASYNC240: no blocking pathlib call inline)."""
    return path.resolve(strict=False)


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
        cell_id=str(leaving.cell_id),
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
    """Print one fixed-width table row per Leaving, its Cell first."""
    typer.echo(
        f"{'CELL':<28}  {'PATH':<40}  {'SHA256':<10}  {'SIZE':>10}  {'APPROVED BY':<11}  "
        f"{'LEFT AT':<26}  REMOVED"
    )
    for row in rows:
        typer.echo(
            f"{row.cell_id:<28}  {row.path:<40}  {row.sha256[:10]:<10}  {row.size:>10}  "
            f"{row.approved_by:<11}  {row.left_at:<26}  {row.removed_at or '-'}"
        )
