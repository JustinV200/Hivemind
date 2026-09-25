"""Define resolve_link: turn a Placement into a WardenLink, acquiring a Virtual Cell if needed.

A `ReuseReal` Placement already names an attached Warden -- `resolve_link` just looks its
`WardenLink` up. A `ProvisionVirtual`/`ReuseDormant` Placement does not exist as an attached
Warden yet, so `resolve_link` calls the `hivemind.queen.deps.VirtualCellProvider` seam
(`QueenDeps.virtual_provider`) roadmap step 5.6 implements; with no provider configured, a Virtual
Placement is a `PlacementError` instead (roadmap step 5.7's own build instructions: "when no
provider is injected, a Virtual placement is a PlacementError with a clear message"). When
`acquire` raises `hivemind.hive.CellProvisionError`, ADR-0028's own Consequences apply: placement
re-enters once with the failed backend's own headroom zeroed (`ProvisionVirtual`) or that one
dormant Cell excluded (`ReuseDormant`, `docs/adr/0029`: "a Cell that fails to resume... is
destroyed and placement falls through to a fresh provision"), and a second failure propagates
uncaught.

The dispatcher lifecycle fix splits the Virtual half in two, so the Queen's tick never awaits a
provision: `authorize_virtual` is what the dispatch pass itself checks before it starts one (a
provider is configured, and the tier's egress point below passes), and `acquire_virtual` is the
acquisition, retry included, that `hivemind.queen.dispatcher.provisions` runs beside the tick.
`resolve_link` is still the two in order, for a caller that may wait. The backend backoff notes
how every attempt fared (`hivemind.queen.dispatcher.backoff`): a provision that fails holds its
backend back before the retry decides, so the retry and every placement after it see the rest,
and one that succeeds ends its backend's run of failed rounds.

Roadmap step 10.3 (ADR-0031): provisioning or resuming a Virtual Cell activates its Comb Shield
tier's egress policy for the task, so before any `acquire` call -- the first or the retry -- the
Queen, acting for the goal, passes the `comb_shield_egress` enforcement point: the task's goal
set, when it carries one, must hold `cell:comb_shield:<tier>` for the tier the Cell will carry.
Placement already excludes a tier the goal lacks, so this refuses only a path that reached here
some other way; a refusal is `guard.denied` on the trail and a `PlacementError` to the dispatcher.
Roadmap steps 10.3a-c: the Guard's floors run at that point for every task, the operator's own
included, with the Cell's tier and the Night Veil facts on the context
(`hivemind.queen.dispatcher.night_veil.tier_context`), and a floor's refusal is final.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready` (resolve_link for an attached Cell,
    authorize_virtual) and `hivemind.queen.dispatcher.provisions` (acquire_virtual). Calls into
    `hivemind.brood_chamber` (Task), `hivemind.guard` (EnforcementPoint), `hivemind.hive`
    (CellProvisionError), `hivemind.queen.authority`, `hivemind.queen.deps` (QueenDeps,
    VirtualCellProvider, WardenLink), `hivemind.queen.dispatcher.backoff` (note_failed,
    note_served), `hivemind.queen.dispatcher.snapshot` (build_forage_view, build_inventory),
    `hivemind.queen.placement` (Placement, PlacementError, ProvisionVirtual, ReuseDormant,
    ReuseReal, VirtualBackendCandidate, decide, rules) and waggle only.

Key invariants:
    - `acquire_virtual` retries at most once: the retry path never calls itself recursively, so
      a backend that fails twice in a row raises `CellProvisionError` straight out of it.
    - The retry path never mutates `deps.virtual_backends`/`.dormant_cells` in place: it builds a
      fresh `Inventory` through `build_inventory`'s own keyword arguments, so a failed provision
      for one task never leaks into the next task's own placement decision.
    - No `VirtualCellProvider.acquire` call happens before the `comb_shield_egress` check passes.
    - Every `VirtualCellProvider.acquire` call's outcome is noted for its backend's backoff before
      anything else sees it: the retry, the caller, or the next placement.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the Consequences this module's
      retry path implements verbatim.
    - docs/adr/0029-overwintering-policy.md for the dormant-resume-failure fallback.
    - hivemind.queen.deps for VirtualCellProvider, the seam this module calls.
    - hivemind.queen.dispatcher.ready and .provisions for this module's two callers.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from hivemind.brood_chamber import Task
from hivemind.guard import CapabilitySet, EnforcementPoint
from hivemind.hive import CellProvisionError
from hivemind.queen.authority import goal_held, request_for
from hivemind.queen.deps import QueenDeps, VirtualCellProvider, WardenLink
from hivemind.queen.dispatcher.backoff import note_failed, note_served
from hivemind.queen.dispatcher.night_veil import tier_context
from hivemind.queen.dispatcher.snapshot import (
    build_forage_view,
    build_inventory,
    current_virtual_backends,
)
from hivemind.queen.placement import (
    Placement,
    PlacementError,
    ProvisionVirtual,
    ReuseDormant,
    ReuseReal,
    VirtualBackendCandidate,
    decide,
    rules,
)
from waggle.ids import WardenId

__all__ = ["acquire_virtual", "authorize_virtual", "resolve_link"]


async def resolve_link(
    deps: QueenDeps, wardens: Sequence[WardenLink], task: Task, placement: Placement
) -> tuple[WardenLink, Placement]:
    """Return the WardenLink `placement` names, acquiring a Virtual Cell first if it needs one.

    Args:
        deps: The Queen's collaborators; `deps.virtual_provider` is the seam a Virtual Placement
            is acquired through.
        wardens: Every attached Warden; a `ReuseReal` Placement must already name one of these.
        task: The task this Cell is being resolved for.
        placement: What `hivemind.queen.placement.decide.decide` chose.

    Returns:
        `(link, effective_placement)`: the resolved `WardenLink`, and the Placement actually used
        -- unchanged unless a failed Virtual acquire triggered one retry (ADR-0028 Consequences).

    Raises:
        PlacementError: `placement` names a Warden that is no longer attached, it is a Virtual
            Placement and `deps.virtual_provider` is unset, or the `comb_shield_egress` point
            refused the Cell's tier for this task's goal (`guard.denied` is already on the trail).
        hivemind.hive.CellProvisionError: The Virtual acquire failed twice in a row (once at the
            original placement, once at the retry with headroom zeroed / the dormant Cell excluded).
    """
    if isinstance(placement, ReuseReal):
        link = _attached(wardens, placement.warden_id)
        if link is None:
            raise PlacementError(
                f"decide() named Warden {placement.warden_id}, which is not currently attached."
            )
        return link, placement
    await authorize_virtual(deps, task, placement)
    return await acquire_virtual(deps, wardens, task, placement)


async def authorize_virtual(
    deps: QueenDeps, task: Task, placement: ProvisionVirtual | ReuseDormant
) -> None:
    """Check a Virtual Placement may be acquired at all: a provider exists and its tier may egress.

    Awaited by the dispatch pass itself, before an acquisition starts beside the tick, so a
    refusal is recorded (and a final one cancels the task) on the very pass that placed it.

    Raises:
        PlacementError: `deps.virtual_provider` is unset, or the `comb_shield_egress` point
            refused the Cell's tier (`guard.denied` is already on the trail).
    """
    if deps.virtual_provider is None:
        raise PlacementError(
            "decide() chose a Virtual Cell but no VirtualCellProvider is configured on QueenDeps "
            "(roadmap step 5.6 wires one); a Virtual placement cannot be acquired without it."
        )
    await _authorize_egress(deps, task, placement)


async def acquire_virtual(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    task: Task,
    placement: ProvisionVirtual | ReuseDormant,
) -> tuple[WardenLink, Placement]:
    """Acquire an authorized Virtual Placement's Cell, retrying once on a failed acquire.

    Args:
        deps: The Queen's collaborators.
        wardens: Every attached Warden, for a retry that falls back to an attached Cell.
        task: The task this Cell is being acquired for.
        placement: A Virtual Placement `authorize_virtual` already passed.

    Returns:
        The Cell's link and the Placement actually used (the retry's, after a failed acquire).

    Raises:
        PlacementError: No provider is configured, or the retry's own placement found no Cell.
        hivemind.hive.CellProvisionError: The acquire failed twice in a row.
    """
    if deps.virtual_provider is None:
        # Unreachable after authorize_virtual; kept so the provider is never assumed.
        raise PlacementError("internal: no VirtualCellProvider to acquire a Virtual Cell with.")
    try:
        link = await _acquire_noted(deps, deps.virtual_provider, placement, task)
    except CellProvisionError as exc:
        # ADR-0028 Consequences: re-enter placement once with the failed backend's own headroom
        # zeroed (or, for a dormant Cell, that Cell excluded), then let a second failure propagate.
        return await _retry_once(deps, wardens, task, placement, exc)
    return link, placement


async def _retry_once(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    task: Task,
    placement: ProvisionVirtual | ReuseDormant,
    failure: CellProvisionError,
) -> tuple[WardenLink, Placement]:
    """Re-run `decide` once, with the failed candidate excluded, and acquire its own result.

    The retry's own `queen.placed` reason names what actually happened: `decide` only sees a
    backend with zeroed headroom (or one dormant Cell fewer) and would otherwise report "no
    headroom", which a real run (2026-09-23) showed reads as a capacity problem when the Cell had
    in fact been provisioned and never dialled back.
    """
    if isinstance(placement, ProvisionVirtual):
        current = await current_virtual_backends(deps)
        zeroed = tuple(_zero(b) if b.name == placement.backend else b for b in current)
        inventory = await build_inventory(
            deps, wardens, virtual_backends=zeroed, goal_id=task.goal_id
        )
    else:
        excluded = frozenset({placement.cell_id})
        inventory = await build_inventory(
            deps, wardens, exclude_dormant=excluded, goal_id=task.goal_id
        )
    decided = decide(
        task.spec.needs, inventory, build_forage_view(deps, task), deps.placement_policy
    )
    retry = dataclasses.replace(decided, reason=_retry_reason(decided.reason, placement, failure))
    if isinstance(retry, ReuseReal):
        link = _attached(wardens, retry.warden_id)
        if link is None:
            raise PlacementError(
                f"decide() named Warden {retry.warden_id} on retry, which is not attached."
            )
        return link, retry
    if deps.virtual_provider is None:
        # Unreachable in practice: reaching this branch means the first attempt already called
        # a provider (the only way `placement` could have been a Virtual Placement at all).
        raise PlacementError("internal: no VirtualCellProvider left to retry an acquire with.")
    await _authorize_egress(deps, task, retry)
    # A second failure propagates (its backend held back first, like the first's).
    link = await _acquire_noted(deps, deps.virtual_provider, retry, task)
    return link, retry


async def _acquire_noted(
    deps: QueenDeps,
    provider: VirtualCellProvider,
    placement: ProvisionVirtual | ReuseDormant,
    task: Task,
) -> WardenLink:
    """Acquire `placement`'s Cell through `provider`, noting how its backend fared (backoff).

    Raises:
        hivemind.hive.CellProvisionError: The acquire failed; its backend is held back first,
            so the ADR-0028 retry, and every placement after it, already sees the rest.
    """
    started = deps.clock.now()
    try:
        link = await provider.acquire(placement, task)
    except CellProvisionError as exc:
        await note_failed(deps, placement, exc, started)
        raise
    # A Cell made: any run of failed rounds on its backend is over.
    await note_served(deps, placement)
    return link


async def _authorize_egress(
    deps: QueenDeps, task: Task, placement: ProvisionVirtual | ReuseDormant
) -> None:
    """Pass the `comb_shield_egress` point for the tier the Virtual Cell will carry, or refuse.

    A fresh provision carries its spec's own tier; a dormant Cell was matched on the task's own
    requested tier (`hivemind.queen.placement.decide._matching_dormant`), so that is its tier.
    The floors run first and for every task (roadmap steps 10.3a-c: the tier's initiation,
    inheritance and control link), since they hold whatever a set says; the goal's set is then
    checked only when the goal carries one.

    Raises:
        PlacementError: A floor refused (final), or the goal's set does not hold
            `cell:comb_shield:<tier>`; the Enforcer has already recorded `guard.denied`.
    """
    tier = (
        placement.spec.comb_shield
        if isinstance(placement, ProvisionVirtual)
        else task.spec.needs.comb_shield
    )
    goal = goal_held(task)
    request = request_for(
        deps,
        EnforcementPoint.COMB_SHIELD_EGRESS,
        rules.tier_capability(tier),
        goal or CapabilitySet.empty(),  # The floors never read it; the operator's path has none.
    ).model_copy(update={"context": await tier_context(deps, task, tier)})
    floor = await deps.enforcer.check_floors(request)
    if floor is not None:
        raise PlacementError(
            f"Task {task.id} may not activate {tier.name}: {floor.reason}", final=True
        )
    if goal is None:
        return  # The operator's own local path: no ceiling to check the tier against.
    decision = await deps.enforcer.check(request)
    if not decision.allowed:
        raise PlacementError(f"Task {task.id} may not activate {tier.name}: {decision.reason}")


def _retry_reason(
    decided: str, placement: ProvisionVirtual | ReuseDormant, failure: CellProvisionError
) -> str:
    """Prefix the retry's own `decide` reason with what the first acquire actually failed on."""
    if isinstance(placement, ProvisionVirtual):
        what = f"backend {placement.backend!r} could not provision a Cell ({failure.reason})"
    else:
        what = f"dormant Cell {placement.cell_id} could not be resumed ({failure.reason})"
    return f"[placement] retry after {what}: {decided}"


def _zero(candidate: VirtualBackendCandidate) -> VirtualBackendCandidate:
    """Return `candidate` with its own headroom zeroed, so `decide` skips it on retry."""
    return dataclasses.replace(
        candidate, capabilities=candidate.capabilities.model_copy(update={"headroom": 0})
    )


def _attached(wardens: Sequence[WardenLink], warden_id: WardenId) -> WardenLink | None:
    """Return the WardenLink named by `warden_id`, or None when it names no attached Warden."""
    return next((link for link in wardens if link.warden_id == warden_id), None)
