"""Provide `hive cells list` and nest `hive cells leavings`: inspect the Hive Stand's Cells.

`hive cells list --manifest hive.toml [--json]` builds only as much of a Hive as `hivemind.cell.
local.HiveStandSource.cells` needs -- the Hive Stand's own config, never a full `hivemind.cli.
compose.build_hive` -- and prints one row per Cell it reports: id, name, source, kind, access
level, Comb Shield tier, capabilities and capacity. The Hive Stand's own Cell is always there; with
`[swarm] enabled` every enrolled device that is not revoked follows it as a Real Cell of source
`swarm` (roadmap step 11.12), read from the swarm registry and the trail by `hivemind.cli.swarm.
cells`, with the three columns only a Swarm Cell fills: where its Warden runs (`hive_stand` at
GATEWAY, `on_cell` once colonized), the rung of the runtime ladder, and the Warden's mode.
`hive cells leavings list|remove` (roadmap step 5.0a) is nested here as `hivemind.cli.readback.
leavings.app`, the same way a Typer sub-app nests under any other group; `hive cells isolate|lift`
(roadmap step 10.6, `hivemind.cli.readback.isolation`) are merged in at this level.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`). Calls into `hivemind.cell`, `hivemind.cell.leavings`
    (`InMemoryLeavingsStore`, roadmap step 5.0a), `hivemind.cli.compose.deps`
    (`build_hive_stand_source`), `hivemind.cli.readback.leavings`, `hivemind.cli.stores` and
    `hivemind.cli.swarm.cells` (the Swarm's Cells) only.

Key invariants:
    - `list` never leases a Cell: `cells()` only reads capabilities and live capacity, the same
      read `hivemind.wardens.warden.Warden.start` makes before actually leasing, and its
      throwaway `InMemoryLeavingsStore` never touches the real Leavings ledger file either. A
      Swarm Cell is read from the registry and the trail and is never contacted.
    - `--json` prints one array element per Cell with the same fields the table shows, never a
      raw `Cell.model_dump()` (codingrules section 12 style: identifiers and sizes, not internals
      like `RealCellLease`-shaped detail this command never touches in the first place).

See Also:
    - .claude/roadmap.md step 3.21 for `list`'s own roadmap bullet; step 5.0a for `leavings`'s.
    - hivemind.cell.local.source for HiveStandSource.cells, this command's one read.
    - hivemind.cli.compose.deps for build_hive_stand_source, the construction this command mirrors
      without building a whole Hive around it.
    - hivemind.cli.readback.leavings for the `leavings list|remove` sub-app nested here.
"""

from __future__ import annotations

import asyncio
import json

import typer
from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import Cell
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.compose.deps import build_hive_stand_source
from hivemind.cli.readback.isolation import app as isolation_app
from hivemind.cli.readback.leavings import app as leavings_app
from hivemind.cli.readback.virtual import app as virtual_app
from hivemind.cli.stores import DEFAULT_MANIFEST, JsonOption, ManifestOption, load_manifest_or_exit
from hivemind.cli.swarm.cells import SwarmCellFacts, read_swarm_cells
from hivemind.manifest import HiveManifest
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock, SystemClock

app = typer.Typer(name="cells", help="List the Cells the Hive Stand (and the Swarm) can offer.")
# Roadmap step 5.13: merges virtual_app's six commands (inspect/destroy/release/snapshot/
# rollback/abscond) straight into this app with no name/prefix of its own (Typer's own add_typer,
# given no name and a nameless sub-app, folds its commands in at this level -- verified empirically
# while drafting hivemind.cli.readback.virtual's own module docstring), so `hive cells inspect ...`
# is the command, never `hive cells virtual inspect ...`.
app.add_typer(virtual_app)
# Roadmap step 5.0a: `hive cells leavings list|remove [CELL]`, nested under this group the same
# way `hive capping <sub>` nests its own subcommands (hivemind.cli.readback.leavings's own app).
app.add_typer(leavings_app, name="leavings")
# Roadmap step 10.6: `hive cells isolate|lift`, the human's two levers, merged in at this level
# the same way virtual_app's are; both act as the Hive Stand's console over `hive serve`.
app.add_typer(isolation_app)

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
        description="Whether this Cell may currently be leased: the Hive Stand's own "
        "`[hive_stand] enabled`, or for a Swarm Cell whether its device is ONLINE."
    )
    warden_runs: str | None = Field(
        description="Where its Warden runs: `hive_stand`, or `on_cell` (a Swarm device whose "
        "Warden has moved onto it); null when not tracked."
    )
    level: str | None = Field(
        description="A Swarm Cell's rung of the runtime ladder (GATEWAY, COLONIZED or NUC); null "
        "for the Hive Stand."
    )
    mode: str | None = Field(
        description="A Swarm Cell's Warden mode from the newest `warden.*` mode edge on the "
        "trail: ACTIVE, WATCH, OFFLINE or UNKNOWN; null for the Hive Stand."
    )


@app.command("list")
def list_command(manifest: ManifestOption = DEFAULT_MANIFEST, as_json: JsonOption = False) -> None:
    """List every Cell the Hive Stand and the Swarm (enrolled devices) can currently offer."""
    loaded = load_manifest_or_exit(manifest)
    cells = asyncio.run(_read_cells(loaded))
    # The Hive Stand's own Cell first, then each enrolled device (nothing when the Swarm is off).
    rows = (
        *(_cell_row(cell, enabled=loaded.hive_stand.enabled) for cell in cells),
        *(_swarm_row(facts) for facts in read_swarm_cells(loaded)),
    )
    if as_json:
        typer.echo(json.dumps([row.model_dump(mode="json") for row in rows], indent=2))
        return
    _print_table(rows)


async def _read_cells(manifest: HiveManifest) -> tuple[Cell, ...]:
    """Build the Hive Stand's own source (no lease, no Warden) and read its Cells."""
    # A fresh, throwaway trail and leavings store: this command never leases (module docstring),
    # so cell.leased/cell.released/cell.left never fire and nothing either would hold outlives
    # this one call.
    trail = MemoryPheromoneTrail(FakeClock())
    source = build_hive_stand_source(manifest, trail, SystemClock(), InMemoryLeavingsStore(trail))
    return await source.cells()


def _cell_row(cell: Cell, *, enabled: bool) -> _CellRow:
    """Build the row of a Cell the Hive Stand reports: its Warden runs on the Hive Stand."""
    return _row(cell, enabled=enabled, warden_runs="hive_stand", level=None, mode=None)


def _swarm_row(facts: SwarmCellFacts) -> _CellRow:
    """Build the row of an enrolled device: its Cell, plus where its Warden runs and its mode."""
    return _row(
        facts.cell,
        enabled=facts.is_online,
        warden_runs=facts.warden_runs,
        level=facts.level.value,
        mode=facts.mode,
    )


def _row(
    cell: Cell, *, enabled: bool, warden_runs: str | None, level: str | None, mode: str | None
) -> _CellRow:
    """Build one `hive cells list` row from a Cell and the columns only some Cells fill."""
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
        warden_runs=warden_runs,
        level=level,
        mode=mode,
    )


def _print_table(rows: tuple[_CellRow, ...]) -> None:
    """Print one fixed-width table row per Cell."""
    typer.echo(
        f"{'ID':<31}  {'NAME':<14}  {'SOURCE':<10}  {'KIND':<8}  {'ACCESS':<10}  "
        f"{'SHIELD':<10}  {'WARDEN':<10}  {'LEVEL':<10}  {'MODE':<8}  {'DISPLAY':<7}  "
        f"{'BROWSER':<7}  {'CORES':>5}  ENABLED"
    )
    for row in rows:
        typer.echo(
            f"{row.id:<31}  {row.name:<14}  {row.source:<10}  {row.kind:<8}  "
            f"{row.access_level:<10}  {row.comb_shield:<10}  {(row.warden_runs or '-'):<10}  "
            f"{(row.level or '-'):<10}  {(row.mode or '-'):<8}  {row.has_display!s:<7}  "
            f"{row.has_browser!s:<7}  {row.cores:>5}  {row.enabled}"
        )
