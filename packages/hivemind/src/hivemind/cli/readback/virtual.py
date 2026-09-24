"""Provide `hive cells inspect/destroy/release/snapshot/rollback/abscond` (roadmap step 5.13).

Six commands merged directly into `hive cells` (`hivemind.cli.readback.cells`'s own Typer app, via
one `add_typer(app)` nesting line with no name -- see this module's own `app` below), output
formatting only, every real decision delegated to `hivemind.hive`, `hivemind.workers.roles.
undertaker` and this package's own `hivemind.cli.readback.virtual_offline` helpers:

- `inspect <cell-id>`: Real or Virtual, decided by "is it in a registered backend's own labelled
  list" (never `cell.kind`, module docstring's own rule, shared with `destroy`) -- works with no
  Queen running, since a backend's `list_cells` reads the infrastructure itself.
- `destroy <cell-id>`: Virtual only; refuses a Real id with a clear message; an unknown id is a
  clean no-op (`hivemind.hive.backends.base.CellBackend.destroy`'s own idempotent contract, run
  through `Undertaker.destroy_virtual`), printing the `cell.destroyed` trail event id.
- `release <lease-id>`: writes a durable `hivemind.queen.cluster.ClusterOrder(kind=RELEASE)` row,
  exactly like `hive cluster`/`hive wake` (docs/adr/0024), for a running Queen's own tick
  (`hivemind.queen.cluster.tick.run_release_tick`) to drain; reports the lease orphaned and points
  at `abscond` when no Queen looks to be running (`virtual_offline.queen_likely_running`'s own
  documented heuristic -- v0 has no live link into a running Queen, `hivemind.cli.readback.
  wardens`'s own module docstring).
- `snapshot <cell-id>` / `rollback <cell-id> <snapshot-id>`: through a `Snapshotter` from
  `hivemind.hive.snapshot.snapshotter_for` (roadmap step 5.10, a concurrent dispatch that landed
  during this one's own gate window -- `virtual_offline.build_snapshotter`'s own docstring covers
  the one cross-process limitation this leaves: a `rollback` invocation cannot see what a separate,
  earlier `snapshot` invocation recorded, since that factory's own snapshotters keep their mapping
  in one process's memory).
- `abscond [--yes]`: from backend labels and the trail alone (no lifecycle table, roadmap step
  5.13's own wording): destroys every Virtual Cell this Hive's id labels across every registered
  backend, releases every still-open Real Cell lease the trail shows (directly, by reconstructing
  its `RealCellLease` from the trail plus its still-present scratch directory, when no Queen looks
  to be running; through a durable release order otherwise -- module docstring's own two paths),
  revokes every live grant the pass touched (read back as one before/after count on the restored
  `ForageLedger`, since `destroy_virtual`/`release_real` do not hand their own count back), and
  prints a summary.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by an operator's shell through the `hive`
    console script (`hivemind.cli.app`, via `hivemind.cli.readback.cells`). Calls into
    `hivemind.cell` (CellIdentity, SnapshotId, SnapshotUnsupportedError), `hivemind.cli.compose.
    deps` (build_hive_stand_source, build_ledger), `hivemind.cli.compose.virtual_cells`
    (build_virtual_cells), `hivemind.cli.readback.virtual_offline`, `hivemind.cli.stores`,
    `hivemind.queen.cluster` (ClusterOrder, OrderKind, new_order_id) and waggle only.

Key invariants:
    - Real/Virtual is always decided by backend membership, never `cell.kind` (module docstring;
      `inspect`/`destroy` share `virtual_offline.virtual_cell_lookup`).
    - `destroy` on an id no backend recognises still calls `Undertaker.destroy_virtual` (its own
      idempotent contract), never raises for that case alone.
    - `abscond` never destroys/releases anything without `--yes` or an interactive confirmation.

See Also:
    - .claude/roadmap.md step 5.13 for this module's own deliverable, verbatim.
    - docs/adr/0024-clustering-protocol.md for why `release` writes a durable order instead of
      calling a running Queen directly.
    - hivemind.cli.readback.virtual_offline for every real read/write this module delegates to.
    - hivemind.workers.roles.undertaker for Undertaker, destroy_virtual and release_real.

Public API:
    - app: this module's own Typer app, merged into `hive cells` with no name of its own.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from hivemind.cell import CellIdentity, SnapshotId, SnapshotUnsupportedError
from hivemind.cell.leavings import InMemoryLeavingsStore, LeavingsStore
from hivemind.cli.compose.deps import build_hive_stand_source, build_ledger
from hivemind.cli.compose.virtual_cells import build_virtual_cells
from hivemind.cli.readback.virtual_abscond import AbscondDeps, AbscondSummary, run_abscond
from hivemind.cli.readback.virtual_offline import (
    LeaseOrphan,
    OfflineCellDeps,
    build_snapshotter,
    build_undertaker,
    open_real_leases,
    placeholder_cell,
    queen_likely_running,
    virtual_cell_lookup,
)
from hivemind.cli.stores import (
    DEFAULT_MANIFEST,
    ManifestOption,
    load_manifest_or_exit,
    open_cluster_orders,
    open_leavings,
    open_trail,
)
from hivemind.hive import BackendRegistry, CellBackend, VirtualCellRecord
from hivemind.manifest import HiveManifest
from hivemind.pheromone import PheromoneTrail
from hivemind.queen.cluster import ClusterOrder, OrderKind, OrderStore, new_order_id
from hivemind.queen.forage.ledger import ForageLedger
from waggle.clock import SystemClock
from waggle.ids import CellId

app = typer.Typer()  # No name: cells.py merges this in with no prefix (module docstring).

__all__ = ["app"]

_YesOption = Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")]


@app.command("inspect")
def inspect_command(
    cell_id: Annotated[str, typer.Argument(help="A Real or Virtual Cell's own id.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Show what is known about CELL_ID: Virtual (from backend labels) or Real (from the trail)."""
    loaded = load_manifest_or_exit(manifest)
    trail = open_trail(loaded.resolve_path(loaded.hive.db))
    asyncio.run(_inspect(loaded, trail, cell_id))


async def _inspect(manifest: HiveManifest, trail: PheromoneTrail, cell_id: str) -> None:
    """Print CELL_ID's own row, Virtual or Real; exit 1 when neither list has it."""
    clock = SystemClock()
    virtual_cells = build_virtual_cells(manifest, trail, clock)
    if virtual_cells is not None:
        found = await virtual_cell_lookup(virtual_cells.registry, manifest.hive.id, cell_id)
        if found is not None:
            _print_virtual_inspect(*found)
            return
    # `inspect` never leases, so nothing can ever write a Leaving through this source: a
    # throwaway ledger is what `build_hive_stand_source`'s own docstring asks for here.
    source = build_hive_stand_source(manifest, trail, clock, InMemoryLeavingsStore(trail))
    real_cell = next((c for c in await source.cells() if c.id == cell_id), None)
    if real_cell is None:
        typer.echo(
            f"No Cell {cell_id!r} found (checked every registered Virtual backend and "
            "the Hive Stand's own Real Cell).",
            err=True,
        )
        raise typer.Exit(code=1)
    leases = tuple(lease for lease in await open_real_leases(trail) if lease.cell_id == cell_id)
    _print_real_inspect(real_cell.id, real_cell.name, real_cell.access_level.name, leases)


def _print_virtual_inspect(backend_name: str, record: VirtualCellRecord) -> None:
    """Print one Virtual Cell's own inspect row."""
    typer.echo(f"id:         {record.cell_id}")
    typer.echo("kind:       VIRTUAL")
    typer.echo(f"backend:    {backend_name}")
    typer.echo(f"image:      {record.image}")
    typer.echo(f"status:     {record.status.value}")
    typer.echo(f"created_at: {record.created_at}")
    typer.echo(f"labels:     {dict(record.labels)}")


def _print_real_inspect(
    cell_id: str, name: str, access_level: str, leases: tuple[LeaseOrphan, ...]
) -> None:
    """Print one Real Cell's own inspect row, plus every lease the trail shows still open."""
    typer.echo(f"id:          {cell_id}")
    typer.echo("kind:        REAL")
    typer.echo(f"name:        {name}")
    typer.echo(f"access:      {access_level}")
    typer.echo(f"open_leases: {len(leases)}")
    for lease in leases:
        typer.echo(f"  - {lease.lease_id} (holder={lease.holder}, task_id={lease.task_id})")


@app.command("destroy")
def destroy_command(
    cell_id: Annotated[str, typer.Argument(help="A Virtual Cell's own id.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Destroy CELL_ID (Virtual only); an unknown id is a clean no-op, a Real id is refused."""
    loaded = load_manifest_or_exit(manifest)
    db_path = loaded.resolve_path(loaded.hive.db)
    trail = open_trail(db_path)
    ledger = build_ledger(loaded, loaded.forage.reserve)
    asyncio.run(_destroy(loaded, trail, ledger, open_leavings(db_path), cell_id))


async def _destroy(
    manifest: HiveManifest,
    trail: PheromoneTrail,
    ledger: ForageLedger,
    leavings: LeavingsStore,
    cell_id: str,
) -> None:
    """Refuse a Real id; otherwise destroy through the Undertaker and print the trail event id."""
    clock = SystemClock()
    source = build_hive_stand_source(manifest, trail, clock, leavings)
    if any(cell.id == cell_id for cell in await source.cells()):
        typer.echo(
            f"{cell_id!r} is the Hive Stand's own Real Cell; `hive cells destroy` only destroys "
            "Virtual Cells (a Real Cell is borrowed, never owned -- CLAUDE.md's own rule).",
            err=True,
        )
        raise typer.Exit(code=1)
    virtual_cells = build_virtual_cells(manifest, trail, clock)
    if virtual_cells is None:
        typer.echo(
            f"[virtual_cells] backend is not configured; nothing to destroy for {cell_id!r}."
        )
        return  # A clean no-op: there is no backend this id could ever be found on.
    identity = CellIdentity(hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system")
    _backend_name, backend = await _resolve_backend(virtual_cells.registry, manifest, cell_id)
    # The real Leavings ledger (roadmap step 5.0a): a destroyed Virtual Cell's own ledgered paths
    # died with it, so the Undertaker marks every active row removed as part of the destroy.
    offline = OfflineCellDeps(trail=trail, clock=clock, identity=identity, leavings=leavings)
    undertaker = build_undertaker(backend, offline, ledger)
    event_id = await undertaker.destroy_virtual(CellId(cell_id))
    typer.echo(event_id)


async def _resolve_backend(
    registry: BackendRegistry, manifest: HiveManifest, cell_id: str
) -> tuple[str, CellBackend]:
    """Return the backend that actually lists `cell_id`, or the manifest's own selected default.

    An unknown id still needs *some* backend to call idempotent `destroy` through (module
    docstring): the manifest's own `[virtual_cells] backend`, when registered, or else whichever
    name `BackendRegistry.register` saw first (always includes "fake" -- `build_virtual_cells`'s
    own module docstring).
    """
    found = await virtual_cell_lookup(registry, manifest.hive.id, cell_id)
    if found is not None:
        name, _record = found
        return name, registry.get(name)
    selected = manifest.virtual_cells.backend
    name = (
        selected if selected is not None and selected in registry.names() else registry.names()[0]
    )
    return name, registry.get(name)


@app.command("release")
def release_command(
    lease_id: Annotated[str, typer.Argument(help="A Real Cell lease's own id.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Ask a running Queen to release LEASE_ID; reports it orphaned when none looks running."""
    loaded = load_manifest_or_exit(manifest)
    db_path = loaded.resolve_path(loaded.hive.db)
    trail = open_trail(db_path)
    orders = open_cluster_orders(db_path)
    asyncio.run(_release(trail, orders, lease_id))


async def _release(trail: PheromoneTrail, orders: OrderStore, lease_id: str) -> None:
    """Write a RELEASE order when a Queen looks to be running; otherwise report it orphaned."""
    leases = await open_real_leases(trail)
    if not any(lease.lease_id == lease_id for lease in leases):
        typer.echo(f"No open lease {lease_id!r} found on the trail.", err=True)
        raise typer.Exit(code=1)
    if not await queen_likely_running(trail):
        typer.echo(
            f"No Queen appears to be running for this Hive (heuristic: the trail's own "
            f"warden.* history); lease {lease_id!r} is orphaned. Run `hive cells abscond` to "
            "release it directly.",
            err=True,
        )
        raise typer.Exit(code=1)
    clock = SystemClock()
    order = ClusterOrder(
        id=new_order_id(clock),
        kind=OrderKind.RELEASE,
        provider=None,
        requested_at=clock.now(),
        lease_id=lease_id,
    )
    await orders.put_order(order)
    typer.echo(order.id)


@app.command("snapshot")
def snapshot_command(
    cell_id: Annotated[str, typer.Argument(help="A Virtual Cell's own id.")],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Snapshot CELL_ID through its own backend's Snapshotter, and print the snapshot id."""
    loaded = load_manifest_or_exit(manifest)
    trail = open_trail(loaded.resolve_path(loaded.hive.db))
    asyncio.run(_snapshot(loaded, trail, cell_id))


async def _snapshot(manifest: HiveManifest, trail: PheromoneTrail, cell_id: str) -> None:
    """Look CELL_ID up across every registered backend and take one snapshot of it."""
    clock = SystemClock()
    backend, record = await _require_virtual(manifest, trail, clock, cell_id)
    section = manifest.virtual_cells
    snapshotter = build_snapshotter(
        backend,
        clock,
        manifest.resolve_path(manifest.hive.db),
        retention_s=section.snapshot_retention_s,
        disk_budget_bytes=section.snapshot_disk_budget_mb * 1024 * 1024,
    )
    snapshot_id = await snapshotter.snapshot(placeholder_cell(record))
    typer.echo(snapshot_id)


@app.command("rollback")
def rollback_command(
    cell_id: Annotated[str, typer.Argument(help="A Virtual Cell's own id.")],
    snapshot_id: Annotated[
        str, typer.Argument(help="A snapshot id `hive cells snapshot` printed.")
    ],
    manifest: ManifestOption = DEFAULT_MANIFEST,
) -> None:
    """Roll CELL_ID back to SNAPSHOT_ID through its own backend's Snapshotter."""
    loaded = load_manifest_or_exit(manifest)
    trail = open_trail(loaded.resolve_path(loaded.hive.db))
    asyncio.run(_rollback(loaded, trail, cell_id, snapshot_id))


async def _rollback(
    manifest: HiveManifest, trail: PheromoneTrail, cell_id: str, snapshot_id: str
) -> None:
    """Look CELL_ID up and roll it back; exits 1 with a clear message on an unsupported/bad id."""
    from hivemind.hive.snapshot import SnapshotNotFoundError  # Lazy: see build_snapshotter's own.

    clock = SystemClock()
    backend, record = await _require_virtual(manifest, trail, clock, cell_id)
    section = manifest.virtual_cells
    snapshotter = build_snapshotter(
        backend,
        clock,
        manifest.resolve_path(manifest.hive.db),
        retention_s=section.snapshot_retention_s,
        disk_budget_bytes=section.snapshot_disk_budget_mb * 1024 * 1024,
    )
    try:
        await snapshotter.rollback(placeholder_cell(record), SnapshotId(snapshot_id))
    except (SnapshotUnsupportedError, SnapshotNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Rolled {cell_id} back to {snapshot_id}.")


async def _require_virtual(
    manifest: HiveManifest, trail: PheromoneTrail, clock: SystemClock, cell_id: str
) -> tuple[CellBackend, VirtualCellRecord]:
    """Return `(backend, record)` for CELL_ID, or exit 1 when no backend lists it."""
    virtual_cells = build_virtual_cells(manifest, trail, clock)
    if virtual_cells is None:
        typer.echo("[virtual_cells] backend is not configured; nothing to snapshot.", err=True)
        raise typer.Exit(code=1)
    found = await virtual_cell_lookup(virtual_cells.registry, manifest.hive.id, cell_id)
    if found is None:
        typer.echo(f"No Virtual Cell {cell_id!r} found on any registered backend.", err=True)
        raise typer.Exit(code=1)
    backend_name, record = found
    return virtual_cells.registry.get(backend_name), record


@app.command("abscond")
def abscond_command(manifest: ManifestOption = DEFAULT_MANIFEST, yes: _YesOption = False) -> None:
    """Destroy every Virtual Cell and release every lease this Hive's data shows; summarise."""
    loaded = load_manifest_or_exit(manifest)
    if not yes:
        confirmed = typer.confirm(
            f"Destroy every Virtual Cell and release every open lease tagged with Hive "
            f"{loaded.hive.id!r}? This cannot be undone."
        )
        if not confirmed:
            raise typer.Exit(code=1)
    db_path = loaded.resolve_path(loaded.hive.db)
    trail = open_trail(db_path)
    ledger = build_ledger(loaded, loaded.forage.reserve)
    orders = open_cluster_orders(db_path)
    summary = asyncio.run(_abscond(loaded, trail, ledger, orders, open_leavings(db_path)))
    _print_abscond_summary(summary)


async def _abscond(
    manifest: HiveManifest,
    trail: PheromoneTrail,
    ledger: ForageLedger,
    orders: OrderStore,
    leavings: LeavingsStore,
) -> AbscondSummary:
    """Build `virtual_cells` (or None) and run the whole abscond pass through it."""
    clock = SystemClock()
    virtual_cells = build_virtual_cells(manifest, trail, clock)
    deps = AbscondDeps(
        manifest=manifest,
        trail=trail,
        ledger=ledger,
        orders=orders,
        clock=clock,
        virtual_cells=virtual_cells,
        leavings=leavings,
    )
    return await run_abscond(deps)


def _print_abscond_summary(summary: AbscondSummary) -> None:
    """Print the operator-facing receipt of one `hive cells abscond` pass."""
    typer.echo(f"containers_destroyed:      {summary.containers_destroyed}")
    typer.echo(f"leases_released:           {summary.leases_released}")
    typer.echo(f"leases_deferred_to_queen:  {summary.leases_deferred_to_queen}")
    typer.echo(f"leases_left_untouched:     {summary.leases_left_untouched}")
    typer.echo(f"grants_revoked:            {summary.grants_revoked}")
    typer.echo(f"left_as_found:             {summary.left_as_found}")
