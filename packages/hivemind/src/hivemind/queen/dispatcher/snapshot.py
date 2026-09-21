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
    acquire`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.memory` (WaxSeverity,
    WaxState), `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.placement`
    (Inventory, ForageView, RealCandidate, VirtualBackendCandidate, WaxMention) and waggle only.

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

from hivemind.cell import Cell, HoneyClearance
from hivemind.forage import RoleFootprint
from hivemind.memory import WaxSeverity, WaxState
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.placement import (
    ForageView,
    Inventory,
    RealCandidate,
    VirtualBackendCandidate,
    WaxMention,
)
from waggle.ids import CellId
from waggle.messages.task import WorkerRole

__all__ = ["build_forage_view", "build_inventory"]


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
    backends = virtual_backends if virtual_backends is not None else deps.virtual_backends
    dormant = tuple(cell for cell in deps.dormant_cells if cell.cell_id not in exclude_dormant)
    return Inventory(
        real=real, virtual_backends=backends, dormant=dormant, blocked=blocked, cautioned=cautioned
    )


def build_forage_view(deps: QueenDeps) -> ForageView:
    """Build the pure ForageView `decide` reads: the placed bee's own RoleFootprint.

    Args:
        deps: The Queen's collaborators; `deps.footprints[WorkerRole.DRONE]` is the only Worker
            role phase 3 implements, matching every other footprint lookup in this dispatch.

    Returns:
        The `ForageView` `decide()` reads for this one placement decision.
    """
    return ForageView(footprint=deps.footprints[WorkerRole.DRONE])


def _real_candidate(link: WardenLink, footprint: RoleFootprint) -> RealCandidate:
    """Build one RealCandidate from an attached WardenLink's own Cell."""
    cell = link.cell
    return RealCandidate(
        warden_id=link.warden_id,
        cell_id=cell.id,
        capabilities=cell.capabilities,
        comb_shield=cell.comb_shield,
        is_hive_stand=cell.source == "hive_stand",
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
