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
task. Roadmap step 10.6a: `goal_id` names the goal being placed, and every active Guard
`PlacementHold` on that goal (the Hive Stand's fallback, where the Queen may not isolate) joins the
blocked Cells for this one decision, exactly as a BLOCK Cell Wax note would: placement data, never
a special case inside `decide`. An attached Night Veil Cell is never a candidate at all: it was
provisioned for one task and is torn down when that task ends (codingrules 8.7, "teardown-only"),
so no other task is ever placed there, isolated or not, and no other task's placement reasons
name it; that is also the hold its isolation needs, since it gets no BLOCK note (codingrules 12).

The dispatcher lifecycle fix makes every figure placement reads live. An attached Cell's room is
measured on its capacity as it stands (`WardenLink.live_capacity`, the Hive Stand's load and free
memory re-read now; a Virtual Cell's spec otherwise) less the grants in force on it: each task
holding a live grant there runs one bee, whatever its grant's ceiling (the ledger releases a grant
once its task ends, `hivemind.queen.forage.grants.release_finished`). Before, the Cell as probed
when the Hive was built was read, so a Cell already full of running work still counted as free.
And a backend's room is less every fresh Cell being provisioned on it right now
(`hivemind.queen.dispatcher.provisions`): an acquisition in flight has reserved room the
lifecycle does not count until its Cell exists, and a placement decided meanwhile must not count
on it too.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready` and `hivemind.queen.dispatcher.
    acquire`. Calls into `hivemind.cell` (HoneyClearance), `hivemind.forage` (ForageCapacity,
    ForageGrant, RoleFootprint), `hivemind.memory` (WaxSeverity, WaxState),
    `hivemind.queen.authority` (goal_held), `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.queen.placement` (Inventory, ForageView, ProvisionVirtual, RealCandidate,
    VirtualBackendCandidate, WaxMention), `QueenDeps.guard.requests` (the Guard's placement holds),
    `QueenDeps.ledger` (the grants in force) and waggle only.

Key invariants:
    - `build_inventory` is the only place in this whole dispatch that awaits `deps.memory.list_wax`
      for a placement decision: `hivemind.queen.placement.decide` itself never touches a store.
    - `has_free_capacity` is a simple, cheap signal (room for one more sub-bee once the grants in
      force are counted, and enough free memory for the placed bee's own footprint), read from the
      Cell's live figures, not a re-run of `hivemind.forage.allocate.grant`; the real grant
      computation still happens once placement has already chosen a Cell
      (`hivemind.queen.dispatcher.ready._send_grant_and_assign`).
    - A backend's headroom is never above what its declared cap leaves once every tracked Cell and
      every fresh Cell still being provisioned on it are counted, each once.
    - No attached Night Veil Cell is ever in an `Inventory`'s Real candidates.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for "the caller precomputes... before
      each decision" and the retry-with-zeroed-headroom Consequence this module's own keyword
      arguments implement.
    - hivemind.queen.placement.inventory for Inventory/ForageView, the shapes this module builds.
    - hivemind.queen.dispatcher.acquire for the one caller of `virtual_backends`/`exclude_dormant`.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Iterable, Sequence

from hivemind.brood_chamber import Task
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.forage import ForageCapacity, ForageGrant, RoleFootprint
from hivemind.hive import VirtualCellSpec
from hivemind.hive.lifecycle import LifecycleDormantCell, LifecycleVirtualBackend
from hivemind.memory import WaxSeverity, WaxState
from hivemind.queen.authority import goal_held
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage.night_veil import night_veil_local_only
from hivemind.queen.placement import (
    DormantCandidate,
    ForageView,
    Inventory,
    NightVeilHostingView,
    ProvisionVirtual,
    RealCandidate,
    VirtualBackendCandidate,
    WaxMention,
)
from waggle.ids import CellId, TaskId
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
    goal_id: TaskId | None = None,
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
        goal_id: The goal the placed task belongs to; the Guard's active holds on it block their
            Cells for this decision (roadmap step 10.6a). None places with no hold applied.

    Returns:
        The `Inventory` `decide()` reads for this one placement decision.
    """
    footprint = deps.footprints[WorkerRole.DRONE]
    blocked, cautioned = await _wax_maps(deps)
    if goal_id is not None:
        # A Cell's own BLOCK note, when it has one, is the mention its placement reason names.
        blocked = {**await _held_for(deps, goal_id), **blocked}
    real = await _real_candidates(deps, wardens, footprint)
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
    `goal_capabilities` (roadmap step 10.3) is the task's goal set, parsed: the ceiling every
    candidate must be allowed by, or None for the operator's own local path.

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
        goal_capabilities=goal_held(task),
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


async def _real_candidates(
    deps: QueenDeps, wardens: Sequence[WardenLink], footprint: RoleFootprint
) -> tuple[RealCandidate, ...]:
    """Return one RealCandidate per attached Warden's Cell, no Night Veil Cell among them."""
    # One bee per task holding a grant in force on a Cell (module docstring), counted once.
    in_use = _bees_in_use(deps.ledger.live_grants())
    # A Night Veil Cell holds only the task it was provisioned for (module docstring).
    return tuple(
        [
            await _real_candidate(link, footprint, in_use[link.cell.id])
            for link in wardens
            if link.cell.comb_shield is not CombShieldLevel.NIGHT_VEIL
        ]
    )


async def _real_candidate(
    link: WardenLink, footprint: RoleFootprint, bees_in_use: int
) -> RealCandidate:
    """Build one RealCandidate from an attached WardenLink's Cell, as it stands right now."""
    cell = link.cell
    # Read live where the link can (normally a handful of fast system reads on this host), so a
    # Cell's load and free memory are today's, never the probe taken when the Hive was built.
    capacity = await link.live_capacity() if link.live_capacity is not None else cell.capacity
    return RealCandidate(
        warden_id=link.warden_id,
        cell_id=cell.id,
        capabilities=cell.capabilities,
        comb_shield=cell.comb_shield,
        is_hive_stand=cell.source == "hive_stand",
        has_free_capacity=_has_free_capacity(capacity, footprint, bees_in_use),
        # Copied for placement, the one caller allowed to branch on it (codingrules 8.7).
        kind=cell.kind,
    )


def _has_free_capacity(capacity: ForageCapacity, footprint: RoleFootprint, in_use: int) -> bool:
    """Return whether a Cell at `capacity`, `in_use` bees busy, has room for one bee more.

    A cheap signal, not a re-run of `hivemind.forage.allocate.grant` (module docstring): room in
    the Cell's own sub-bee cap once its grants in force are counted, and enough free host memory
    for the placed bee's own footprint.
    """
    has_a_bee_left = capacity.max_sub_bees - in_use >= 1
    return has_a_bee_left and capacity.host.memory_free_bytes >= footprint.memory_bytes


def _bees_in_use(grants: Iterable[ForageGrant]) -> Counter[str]:
    """Count, per Cell, the tasks its grants in force are for: each runs one bee there.

    Counted by task, not by grant or by ceiling: a retried task holds a grant per attempt until
    it ends, and a ceiling is only the most a Warden may spawn (`zero_grant.hold_for_room`'s own
    rule for a goal's allowance).
    """
    holders = {(grant.cell_id, grant.task_id or grant.id) for grant in grants}
    return Counter(cell_id for cell_id, _holder in holders)


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


async def _held_for(deps: QueenDeps, goal_id: TaskId) -> dict[CellId, WaxMention]:
    """Read the Guard's active placement holds on `goal_id`, as the Cells they block."""
    held: dict[CellId, WaxMention] = {}
    for hold in await deps.guard.requests.holds():
        if hold.is_active and goal_id in hold.goal_ids:
            text = f"placement of goal {goal_id} held here by Guard report {hold.report_id}"
            held[CellId(hold.cell_id)] = WaxMention(id=hold.report_id, text=text)
    return held


async def current_virtual_backends(deps: QueenDeps) -> tuple[VirtualBackendCandidate, ...]:
    """Return the Virtual backends placement sees right now, less the room provisions will take.

    The live source when wired, else the static tuple. The retry-once path
    (`hivemind.queen.dispatcher.acquire`) zeroes one backend's headroom in a copy of THIS, never
    of `deps.virtual_backends` alone: in a real Hive the static tuple is empty and only the live
    source names the configured backend, so zeroing the static tuple made the retry see no
    Virtual side at all and fall back to the Hive Stand (the first real Docker run).
    """
    if deps.virtual_backend_source is not None:
        backends = await deps.virtual_backend_source()
    else:
        backends = deps.virtual_backends
    reserved = _provisioning_by_backend(deps)
    return tuple(_less_room(backend, reserved[backend.name]) for backend in backends)


def _provisioning_by_backend(deps: QueenDeps) -> Counter[str]:
    """Count the fresh Cells being provisioned right now, per backend: room already reserved.

    Only an acquisition still in flight counts: once it ends, its Cell is one the lifecycle
    tracks (and counts) or none at all, so counting it here too would count it twice. Resuming a
    dormant Cell takes no fresh room: the lifecycle already counts that Cell.
    """
    jobs = deps.dispatch.provisions.jobs.values()
    return Counter(
        job.placement.backend
        for job in jobs
        if isinstance(job.placement, ProvisionVirtual) and not job.job.done()
    )


def _less_room(backend: VirtualBackendCandidate, reserved: int) -> VirtualBackendCandidate:
    """Return `backend` with `reserved` Cells' worth less headroom (none declared: unchanged)."""
    headroom = backend.capabilities.headroom
    if reserved == 0 or headroom is None:
        return backend
    left = max(0, headroom - reserved)
    capabilities = backend.capabilities.model_copy(update={"headroom": left})
    return dataclasses.replace(backend, capabilities=capabilities)
