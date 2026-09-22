"""Define run_abscond: `hive cells abscond`'s own orchestration, from backend labels and the trail.

Roadmap step 5.13's own wording for `abscond`: "destroy every Virtual Cell tagged with this Hive id
and release every lease, from backend labels and the trail alone" -- no lifecycle table, since the
whole point is recovering a Hive whose own database may be all that survived, or whose Queen is not
running to help. `run_abscond` is that one pass: destroy every Virtual Cell every registered backend
still lists for this Hive (`hivemind.cli.readback.virtual_offline.list_all_virtual`, through the
Undertaker, idempotent and retried on its own), then either write a durable RELEASE order per still-
open Real Cell lease (when a Queen looks to be running -- forcing a live Warden's own in-process
lease closed from outside it would be unsafe) or reconstruct and release each one directly (when it
does not, and its own scratch directory still exists -- `virtual_offline.reconstruct_lease`'s own
documented, minimal-safe-thing gap: a lease whose scratch is already gone is left for a human, the
same as `hivemind.workers.roles.undertaker.sweep.sweep_orphans` leaves an unresolvable lease id).
`grants_revoked` is read back as one before/after count on the restored `ForageLedger`, since
neither `Undertaker.destroy_virtual` nor `.release_real` hands its own revoked-grant count back to
its caller. `left_as_found` is a fresh re-read after every effect, not a running tally: True only
when nothing this pass could find is still there to find.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.readback`. Called only by
    `hivemind.cli.readback.virtual`'s own `abscond` command. Calls into `hivemind.cell`
    (CellIdentity), `hivemind.cli.readback.virtual_offline`, `hivemind.hive.backends.fake`
    (FakeCellBackend), `hivemind.manifest` (HiveManifest), `hivemind.pheromone` (PheromoneTrail),
    `hivemind.queen.cluster` (ClusterOrder, OrderKind, OrderStore, new_order_id),
    `hivemind.queen.forage.ledger` (ForageLedger) and waggle only.

Key invariants:
    - A Real Cell lease is either deferred to a running Queen (a durable order) or released
      directly; never both, and never silently dropped -- every orphan `open_real_leases` finds
      lands in exactly one of `leases_deferred_to_queen`/`leases_released`/`leases_left_untouched`.
    - `grants_revoked` is never negative (`max(0, before - after)`): a grant some other process
      issues between the two reads would otherwise make the subtraction go the wrong way.

See Also:
    - .claude/roadmap.md step 5.13's own exit criterion: "`hive cells abscond` leaves zero
      containers, zero open leases, zero live grants, and the left-as-found snapshot holds."
    - hivemind.cli.readback.virtual_offline for every read/write this module composes.
    - hivemind.workers.roles.undertaker for Undertaker, destroy_virtual and release_real.

Public API:
    - AbscondDeps, AbscondSummary, run_abscond.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import CellIdentity
from hivemind.cli.compose.virtual_cells import VirtualCellsParts
from hivemind.cli.readback.virtual_offline import (
    build_undertaker,
    list_all_virtual,
    open_real_leases,
    queen_likely_running,
    reconstruct_lease,
)
from hivemind.hive import VirtualCellRecord
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.manifest import HiveManifest
from hivemind.pheromone import PheromoneTrail
from hivemind.queen.cluster import ClusterOrder, OrderKind, OrderStore, new_order_id
from hivemind.queen.forage.ledger import ForageLedger
from waggle.clock import Clock

__all__ = ["AbscondDeps", "AbscondSummary", "run_abscond"]


@dataclass(frozen=True, slots=True)
class AbscondDeps:
    """Every collaborator one `hive cells abscond` pass needs, bundled per codingrules 5.1.

    Attributes:
        manifest: The loaded Hive Manifest; `virtual_cells`, `hive_stand.scratch_root` and
            `hive.id`/`node_id` are what this reads.
        trail: Where every backend-label and `cell.leased`/`cell.released` read comes from, and
            where every `Undertaker` effect records its own event.
        ledger: A `ForageLedger` already restored from this Hive's own durable store
            (`hivemind.cli.compose.deps.build_ledger`); every grant revocation writes through it.
        orders: Where a RELEASE order lands for each lease deferred to a running Queen.
        clock: Source of every id minted and every timestamp this pass records.
        virtual_cells: `hivemind.cli.compose.virtual_cells.build_virtual_cells`'s own return
            value; `None` when `[virtual_cells] backend` is unset, in which case this pass never
            touches a Virtual Cell at all.
    """

    manifest: HiveManifest
    trail: PheromoneTrail
    ledger: ForageLedger
    orders: OrderStore
    clock: Clock
    virtual_cells: VirtualCellsParts | None


@dataclass(frozen=True, slots=True)
class AbscondSummary:
    """What one `run_abscond` pass did, and whether the Hive now looks left as found.

    Attributes:
        containers_destroyed: Virtual Cells destroyed this pass.
        leases_released: Real Cell leases released directly, offline.
        leases_deferred_to_queen: Real Cell leases handed to a running Queen via a RELEASE order,
            not yet actually released by this pass itself.
        leases_left_untouched: Real Cell leases neither released nor deferred: no Queen looked to
            be running, and the lease's own scratch directory was already gone (module docstring's
            own "minimal safe thing" -- nothing here can restore a fact this process never saw).
        grants_revoked: Live Forage grants moved to REVOKED this pass.
        left_as_found: True only when a fresh re-read, after every effect above, finds zero
            Virtual Cells on every registered backend and zero still-open leases on the trail.
    """

    containers_destroyed: int
    leases_released: int
    leases_deferred_to_queen: int
    leases_left_untouched: int
    grants_revoked: int
    left_as_found: bool


async def run_abscond(deps: AbscondDeps) -> AbscondSummary:
    """Destroy every Virtual Cell and close every open Real Cell lease this Hive's own data shows.

    Args:
        deps: Every collaborator this pass needs.

    Returns:
        A summary of what happened, and whether the Hive now looks left as found.
    """
    identity = CellIdentity(
        hive_id=deps.manifest.hive.id, node_id=deps.manifest.hive.node_id, actor="system"
    )
    before_grants = len(deps.ledger.live_grants())
    containers_destroyed = await _destroy_every_virtual(deps, identity)
    released, deferred, untouched = await _release_every_lease(deps, identity)
    grants_revoked = max(0, before_grants - len(deps.ledger.live_grants()))

    remaining_virtual = await _virtual_rows(deps)
    remaining_leases = await open_real_leases(deps.trail)
    return AbscondSummary(
        containers_destroyed=containers_destroyed,
        leases_released=released,
        leases_deferred_to_queen=deferred,
        leases_left_untouched=untouched,
        grants_revoked=grants_revoked,
        left_as_found=not remaining_virtual and not remaining_leases,
    )


async def _destroy_every_virtual(deps: AbscondDeps, identity: CellIdentity) -> int:
    """Destroy every Virtual Cell every registered backend lists for this Hive; return how many."""
    if deps.virtual_cells is None:
        return 0  # No [virtual_cells] backend configured: nothing this Hive could have provisioned.
    rows = await _virtual_rows(deps)
    for backend_name, record in rows:
        backend = deps.virtual_cells.registry.get(backend_name)
        undertaker = build_undertaker(backend, deps.trail, deps.clock, identity, deps.ledger)
        await undertaker.destroy_virtual(record.cell_id)
    return len(rows)


async def _virtual_rows(deps: AbscondDeps) -> tuple[tuple[str, VirtualCellRecord], ...]:
    """Return every `(backend_name, VirtualCellRecord)` this Hive's registered backends list."""
    if deps.virtual_cells is None:
        return ()
    return await list_all_virtual(deps.virtual_cells.registry, deps.manifest.hive.id)


async def _release_every_lease(deps: AbscondDeps, identity: CellIdentity) -> tuple[int, int, int]:
    """Release, defer or leave untouched every open lease; return `(released, deferred, left)`."""
    orphans = await open_real_leases(deps.trail)
    if not orphans:
        return 0, 0, 0
    if await queen_likely_running(deps.trail):
        for orphan in orphans:
            await deps.orders.put_order(
                ClusterOrder(
                    id=new_order_id(deps.clock),
                    kind=OrderKind.RELEASE,
                    provider=None,
                    requested_at=deps.clock.now(),
                    lease_id=orphan.lease_id,
                )
            )
        return 0, len(orphans), 0

    scratch_base = deps.manifest.resolve_path(deps.manifest.hive_stand.scratch_root)
    # Unused by release_real (module docstring's own AbscondDeps note); only satisfies
    # UndertakerDeps.backend's own required shape.
    undertaker = build_undertaker(
        FakeCellBackend(deps.clock), deps.trail, deps.clock, identity, deps.ledger
    )
    released = untouched = 0
    for orphan in orphans:
        scratch_root = scratch_base / orphan.lease_id
        if not scratch_root.exists():
            untouched += 1
            continue
        lease = reconstruct_lease(orphan, scratch_root, deps.trail, deps.clock, identity)
        await undertaker.release_real(lease)
        released += 1
    return released, 0, untouched
