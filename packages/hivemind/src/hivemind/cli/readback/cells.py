"""Provide `hive cells list`: inspect the Cells the Hive Stand (and later the Swarm) can offer.

`hive cells list --manifest hive.toml [--json]` builds only as much of a Hive as `hivemind.cell.
local.HiveStandSource.cells` needs -- the Hive Stand's own config, never a full `hivemind.cli.
compose.build_hive` -- and prints one row per Cell it reports: id, name, source, kind, access
level, Comb Shield tier, capabilities and capacity. v0 has exactly one Cell (the Hive Stand's own);
this command's own row shape already covers a Swarm-sourced Cell too (`hivemind.swarm`, a later
phase), since `hivemind.cell.Cell` makes no distinction a reader of this table would need to see.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.cell`, `hivemind.cli.compose.deps`
    (`build_hive_stand_source`) and `hivemind.cli.stores` only.

Key invariants:
    - Never leases a Cell: `cells()` only reads capabilities and live capacity, the same read
      `hivemind.wardens.warden.Warden.start` makes before actually leasing.
    - `--json` prints one array element per Cell with the same fields the table shows, never a
      raw `Cell.model_dump()` (codingrules section 12 style: identifiers and sizes, not internals
      like `RealCellLease`-shaped detail this command never touches in the first place).

See Also:
    - .claude/roadmap.md step 3.21 for this command's own roadmap bullet.
    - hivemind.cell.local.source for HiveStandSource.cells, this command's one read.
    - hivemind.cli.compose.deps for build_hive_stand_source, the construction this command mirrors
      without building a whole Hive around it.
"""

from __future__ import annotations

import asyncio
import json

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import Cell
from hivemind.cli.compose.deps import build_hive_stand_source
from hivemind.cli.stores import DEFAULT_MANIFEST, JsonOption, ManifestOption, load_manifest_or_exit
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock, SystemClock

app = typer.Typer(name="cells", help="List the Cells the Hive Stand (and the Swarm) can offer.")

__all__ = ["app"]


class _CellRow(BaseModel):
    """One `hive cells list` row: a Cell's identity, tiers, capabilities and capacity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(description="The Cell's own id.")
    name: str = Field(description="A human-readable label.")
    source: str = Field(description="Which RealCellSource produced it (`hive_stand`, `swarm`).")
    kind: str = Field(description="REAL or VIRTUAL.")
    access_level: str = Field(description="The most access any lease on this Cell may hold.")
    comb_shield: str = Field(description="This Cell's own security tier.")
    has_display: bool = Field(description="Whether this Cell can show a display.")
    has_browser: bool = Field(description="Whether this Cell has a browser available.")
    cores: int = Field(description="Logical cores this Cell's HostCapacity reports.")
    memory_free_bytes: int = Field(description="Free memory, in bytes, right now.")
    disk_free_bytes: int = Field(description="Free disk, in bytes, right now.")
    enabled: bool = Field(
        description="Whether this Cell may currently be leased (the Hive Stand's own "
        "`[hive_stand] enabled`; always True for a Swarm Cell, a later phase's own concern)."
    )


@app.command("list")
def list_command(manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False) -> None:
    """List every Cell the Hive Stand (and, later, the Swarm) can currently offer."""
    loaded = load_manifest_or_exit(manifest)
    cells = asyncio.run(_read_cells(loaded))
    rows = tuple(_cell_row(cell, enabled=loaded.hive_stand.enabled) for cell in cells)
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_table(rows)


async def _read_cells(manifest: HiveManifest) -> tuple[Cell, ...]:
    """Build the Hive Stand's own source (no lease, no Warden) and read its Cells."""
    # A fresh, throwaway trail: this command never leases (module docstring), so cell.leased/
    # cell.released never fire and nothing this trail would hold outlives this one call.
    trail = MemoryPheromoneTrail(FakeClock())
    source = build_hive_stand_source(manifest, trail, SystemClock())
    return await source.cells()


def _cell_row(cell: Cell, *, enabled: bool) -> _CellRow:
    """Build one `hive cells list` row from a Cell."""
    capacity = cell.capacity.host
    return _CellRow(
        id=cell.id,
        name=cell.name,
        source=cell.source,
        kind=cell.kind.value,
        access_level=cell.access_level.name,
        comb_shield=cell.comb_shield.name,
        has_display=cell.capabilities.has_display,
        has_browser=cell.capabilities.has_browser,
        cores=capacity.cores,
        memory_free_bytes=capacity.memory_free_bytes,
        disk_free_bytes=capacity.disk_free_bytes,
        enabled=enabled,
    )


def _print_table(rows: tuple[_CellRow, ...]) -> None:
    """Print one fixed-width table row per Cell."""
    typer.echo(
        f"{'ID':<30}  {'NAME':<14}  {'SOURCE':<10}  {'KIND':<8}  {'ACCESS':<10}  "
        f"{'SHIELD':<10}  {'DISPLAY':<7}  {'BROWSER':<7}  {'CORES':>5}  ENABLED"
    )
    for row in rows:
        typer.echo(
            f"{row.id:<30}  {row.name:<14}  {row.source:<10}  {row.kind:<8}  "
            f"{row.access_level:<10}  {row.comb_shield:<10}  {row.has_display!s:<7}  "
            f"{row.has_browser!s:<7}  {row.cores:>5}  {row.enabled}"
        )
