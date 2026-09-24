"""Build the pure `Inventory`/`ForageView` snapshot `hivemind.queen.placement.decide` reads.

ADR-0028: "`decide` performs no I/O: the caller precomputes blocked and cautioned Cells, headroom
and the dormant list." This module is that caller's own I/O: it reads every attached Warden's own
Cell (already in hand, no I/O of its own), queries Cell Wax through `QueenDeps.memory.list_wax`
(the one genuinely awaited call here), and reads `QueenDeps.virtual_backends`/`.dormant_cells`
(plain data QueenDeps already holds, no I/O either -- roadmap steps 5.6/5.9 are the first to
populate them with anything beyond the empty defaults). `build_inventory`'s own `virtual_backends`/
`exclude_dormant` keyword arguments are the retry-once-with-zeroed-headroom path ADR-0028's own
Consequences names: `hivemind.queen.dispatcher.acquire` passes a narrowed copy rather than mutating
`deps` itself, so a failed provision never leaks into the next placement decision for a different
task.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready` and `hivemind.queen.dispatcher.
    acquire`. Calls into `hivemind.cell` (HIVE_STAND_SOURCE, HoneyClearance), `hivemind.memory`
    (WaxSeverity, WaxState), `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.
    placement` (Inventory, ForageView, RealCandidate, VirtualBackendCandidate, WaxMention) and
    waggle only.

Key invariants:
    - `build_inventory` is the only place in this whole dispatch that awaits `deps.memory.list_wax`
      for a placement decision: `hivemind.queen.placement.decide` itself never touches a store.
    - `has_free_capacity` is a simple, cheap signal (room for one more sub-bee, and enough free
      memory for the placed bee's own footprint), not a re-run of `hivemind.forage.allocate.grant`;
      the real grant computation still happens once placement has already chosen a Cell
      (`hivemind.queen.dispatcher.ready._send_grant_and_assign`).

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for "the caller precomputes... before
      each decision" and the retry-with-zeroed-headroom Consequence this module's own keyword
      arguments implement.
    - hivemind.queen.placement.inventory for Inventory/ForageView, the shapes this module builds.
    - hivemind.queen.dispatcher.acquire for the one caller of `virtual_backends`/`exclude_dormant`.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import Task
from hivemind.cell import HIVE_STAND_SOURCE, Cell, HoneyClearance
from hivemind.forage import RoleFootprint
from hivemind.hive import VirtualCellSpec
from hivemind.hive.lifecycle import LifecycleDormantCell, LifecycleVirtualBackend
from hivemind.memory import WaxSeverity, WaxState
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage.night_veil import night_veil_local_only
from hivemind.queen.placement import (
    DormantCandidate,
    ForageView,
    Inventory,
    NightVeilHostingView,
    RealCandidate,
    VirtualBackendCandidate,
    WaxMention,
)
from waggle.ids import CellId
from waggle.messages.task import WorkerRole

__all__ = [
    "build_forage_view",
    "build_inventory",
    "current_virtual_backends",
    "dormant_candidate_from_lifecycle",
    "virtual_backend_candidate_from_lifecycle",
]


async def build_inventory(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    *,
    virtual_backends: tuple[VirtualBackendCandidate, ...] | None = None,
    exclude_dormant: frozenset[CellId] = frozenset(),
) -> Inventory:
    """Build the pure Inventory snapshot `decide` reads, doing every bit of I/O `decide` cannot.

    Args:
        deps: The Queen's collaborators: `deps.memory` for Cell Wax, `deps.footprints` for the
            footprint `has_free_capacity` is measured against, `deps.virtual_backends`/
            `deps.dormant_cells` for the Virtual side (empty until roadmap steps 5.6/5.9 wire
            them).
        wardens: Every attached Warden; one `RealCandidate` per Cell it owns.
        virtual_backends: Overrides `deps.virtual_backends`, for the retry-once-with-zeroed-
            headroom path (ADR-0028 Consequences) -- `hivemind.queen.dispatcher.acquire` passes a
            copy with one backend's headroom zeroed rather than mutating `deps` itself.
        exclude_dormant: Dormant Cell ids to drop from `deps.dormant_cells`, for the same retry
            path when a `ReuseDormant` Placement's own resume failed (`docs/adr/0029`: "a Cell that
            fails to resume within the ready timeout is destroyed and placement falls through").

    Returns:
        The `Inventory` `decide()` reads for this one placement decision.
    """
    footprint = deps.footprints[WorkerRole.DRONE]
    blocked, cautioned = await _wax_maps(deps)
    real = tuple(_real_candidate(link, footprint) for link in wardens)
    if virtual_backends is not None:
        backends = virtual_backends  # The retry-once-with-zeroed-headroom path always wins.
    else:
        backends = await current_virtual_backends(deps)
    if deps.dormant_cell_source is not None:
        live_dormant = await deps.dormant_cell_source()
        dormant = tuple(cell for cell in live_dormant if cell.cell_id not in exclude_dormant)
    else:
        dormant = tuple(cell for cell in deps.dormant_cells if cell.cell_id not in exclude_dormant)
    return Inventory(
        real=real, virtual_backends=backends, dormant=dormant, blocked=blocked, cautioned=cautioned
    )


def build_forage_view(deps: QueenDeps, task: Task) -> ForageView:
    """Build the pure ForageView `decide` reads: the footprint plus the Night Veil facts.

    Roadmap step 5.7a (this branch, closing a gap an earlier implementer's own report named):
    `request_origin` and `night_veil_hosting` used to sit at their permissive defaults forever,
    since nothing threaded the real values through from here. `request_origin` is simply `task.
    spec.origin` (`hivemind.queen.placement.policy.check_night_veil`'s own "who asked" input).
    `night_veil_hosting` stays at its own permissive default (`hivemind.queen.placement.policy.
    NightVeilHostingView`'s own docstring: "not yet knowable, nothing known to violate") unless
    `task.cell_id` already names a Cell with a written `HostingPlan` -- true only when `decide` is
    re-run for a task still nominally tied to one (a retry, or a re-dispatch after Clustering),
    never on a fresh NIGHT_VEIL provision's first decide() call, which has no Cell yet to check.

    Args:
        deps: The Queen's collaborators; `deps.footprints[WorkerRole.DRONE]` is the only Worker
            role phase 3 implements, matching every other footprint lookup in this dispatch.
            `deps.ledger`/`deps.map` are read only when `task.cell_id` is already set.
        task: The task this placement decision is for.

    Returns:
        The `ForageView` `decide()` reads for this one placement decision.
    """
    return ForageView(
        footprint=deps.footprints[WorkerRole.DRONE],
        request_origin=task.spec.origin,
        night_veil_hosting=_night_veil_hosting(deps, task),
    )


def _night_veil_hosting(deps: QueenDeps, task: Task) -> NightVeilHostingView:
    """Check `task.cell_id`'s own written HostingPlan, or fall back to the permissive default.

    Falls back when no Cell -- or no plan for one -- is known yet; see `build_forage_view`'s own
    docstring for exactly when that is.
    """
    if task.cell_id is None:
        return NightVeilHostingView()
    plan = deps.ledger.decisions.plan_for(task.cell_id)
    if plan is None:
        return NightVeilHostingView()
    violations = night_veil_local_only(plan, deps.map)
    return NightVeilHostingView(all_local=not violations, non_local_slots=violations)


def virtual_backend_candidate_from_lifecycle(
    backend: LifecycleVirtualBackend, specs: tuple[VirtualCellSpec, ...] = ()
) -> VirtualBackendCandidate:
    """Convert one hive-layer `LifecycleVirtualBackend` into a queen-layer candidate.

    `hive` (Layer 3) may never import `queen` (Layer 6), so `hivemind.hive.lifecycle.
    CellLifecycle.virtual_backend_candidates` returns its own hive-layer value instead; this is
    where the conversion happens, on the Layer-6 side that already imports both (module docstring:
    "converting the lifecycle's hive-layer candidate types to queen.placement.inventory types in
    snapshot.py"). `hivemind.cli.compose` calls this once per backend, per tick, to build the
    closure it sets on `QueenDeps.virtual_backend_source`.

    Args:
        backend: One backend, as `CellLifecycle.virtual_backend_candidates` reports it (name and
            remaining headroom only -- it tracks no `VirtualCellSpec`s of its own).
        specs: The `VirtualCellSpec`s this backend can provision right now, in preference order;
            `hive.lifecycle` carries none of its own (module docstring's own key invariant on
            `LifecycleVirtualBackend`), so the composition root supplies them from the manifest's
            own `[virtual_cells]` section. Defaults to empty, matching a caller that has none yet.

    Returns:
        The equivalent `VirtualBackendCandidate`.
    """
    return VirtualBackendCandidate(
        name=backend.name, capabilities=backend.capabilities, specs=specs
    )


def dormant_candidate_from_lifecycle(cell: LifecycleDormantCell) -> DormantCandidate:
    """Convert one hive-layer `LifecycleDormantCell` into a queen-layer `DormantCandidate`.

    See `virtual_backend_candidate_from_lifecycle`'s own docstring for why this conversion lives
    here rather than in `hive.lifecycle` itself.

    Args:
        cell: One dormant Cell, as `CellLifecycle.dormant_candidates` reports it.

    Returns:
        The equivalent `DormantCandidate`.
    """
    return DormantCandidate(
        cell_id=cell.cell_id,
        warden_id=cell.warden_id,
        image=cell.image,
        comb_shield=cell.comb_shield,
    )


def _real_candidate(link: WardenLink, footprint: RoleFootprint) -> RealCandidate:
    """Build one RealCandidate from an attached WardenLink's own Cell."""
    cell = link.cell
    return RealCandidate(
        warden_id=link.warden_id,
        cell_id=cell.id,
        capabilities=cell.capabilities,
        comb_shield=cell.comb_shield,
        is_hive_stand=cell.source == HIVE_STAND_SOURCE,
        has_free_capacity=_has_free_capacity(cell, footprint),
    )


def _has_free_capacity(cell: Cell, footprint: RoleFootprint) -> bool:
    """Return whether `cell` has room for one more bee at `footprint`'s own cost.

    A cheap signal, not a re-run of `hivemind.forage.allocate.grant` (module docstring): room in
    the Cell's own sub-bee cap, and enough free host memory for the placed bee's own footprint.
    """
    capacity = cell.capacity
    return capacity.max_sub_bees >= 1 and capacity.host.memory_free_bytes >= footprint.memory_bytes


async def _wax_maps(
    deps: QueenDeps,
) -> tuple[dict[CellId, WaxMention], dict[CellId, WaxMention]]:
    """Read every WRITTEN Cell Wax note, split into blocked (BLOCK) and cautioned (CAUTION)."""
    # HoneyClearance.C2 (the Queen's own full allowance): mirrors every other Queen-side
    # `list_wax` call (`hivemind.queen.ticks.wax`, `hivemind.queen.awake.episode`).
    written = await deps.memory.list_wax(None, frozenset({WaxState.WRITTEN}), HoneyClearance.C2)
    blocked: dict[CellId, WaxMention] = {}
    cautioned: dict[CellId, WaxMention] = {}
    for wax in written:
        mention = WaxMention(id=wax.id, text=wax.text)
        if wax.severity is WaxSeverity.BLOCK:
            blocked[wax.cell_id] = mention
        elif wax.severity is WaxSeverity.CAUTION:
            cautioned[wax.cell_id] = mention
    return blocked, cautioned


async def current_virtual_backends(deps: QueenDeps) -> tuple[VirtualBackendCandidate, ...]:
    """Return the Virtual backends placement sees right now.

    The live source when wired, else the static tuple. The retry-once path
    (`hivemind.queen.dispatcher.acquire`) zeroes one backend's headroom in a copy of THIS, never
    of `deps.virtual_backends` alone: in a real Hive the static tuple is empty and only the live
    source names the configured backend, so zeroing the static tuple made the retry see no
    Virtual side at all and fall back to the Hive Stand (the first real Docker run).
    """
    if deps.virtual_backend_source is not None:
        return await deps.virtual_backend_source()
    return deps.virtual_backends
