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
    sub-package. Called by `hivemind.queen.dispatcher.ready._dispatch_one`. Calls into
    `hivemind.brood_chamber` (Task), `hivemind.guard` (EnforcementPoint), `hivemind.hive`
    (CellProvisionError), `hivemind.queen.authority`, `hivemind.queen.deps` (QueenDeps,
    WardenLink), `hivemind.queen.dispatcher.snapshot` (build_forage_view, build_inventory),
    `hivemind.queen.placement` (Placement, PlacementError, ProvisionVirtual, ReuseDormant,
    ReuseReal, VirtualBackendCandidate, decide, rules) and waggle only.

Key invariants:
    - `resolve_link` retries at most once: the retry path never calls itself recursively, so a
      backend that fails twice in a row raises `CellProvisionError` straight out of this dispatch.
    - The retry path never mutates `deps.virtual_backends`/`.dormant_cells` in place: it builds a
      fresh `Inventory` through `build_inventory`'s own keyword arguments, so a failed provision
      for one task never leaks into the next task's own placement decision.
    - No `VirtualCellProvider.acquire` call happens before the `comb_shield_egress` check passes.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the Consequences this module's
      retry path implements verbatim.
    - docs/adr/0029-overwintering-policy.md for the dormant-resume-failure fallback.
    - hivemind.queen.deps for VirtualCellProvider, the seam this module calls.
    - hivemind.queen.dispatcher.ready for _dispatch_one, this module's one caller.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from hivemind.brood_chamber import Task
from hivemind.guard import CapabilitySet, EnforcementPoint
from hivemind.hive import CellProvisionError
from hivemind.queen.authority import goal_held, request_for
from hivemind.queen.deps import QueenDeps, WardenLink
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

__all__ = ["resolve_link"]


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
        PlacementError: `placement` names a Warden that is no longer attached, or it is a Virtual
            Placement and `deps.virtual_provider` is unset.
        hivemind.hive.CellProvisionError: The Virtual acquire failed twice in a row (once at the
            original placement, once at the retry with headroom zeroed / the dormant Cell excluded).
        PlacementError: The `comb_shield_egress` point refused the Cell's tier for this task's
            goal; `guard.denied` is already on the trail.
    """
    if isinstance(placement, ReuseReal):
        link = _attached(wardens, placement.warden_id)
        if link is None:
            raise PlacementError(
                f"decide() named Warden {placement.warden_id}, which is not currently attached."
            )
        return link, placement
    if deps.virtual_provider is None:
        raise PlacementError(
            "decide() chose a Virtual Cell but no VirtualCellProvider is configured on QueenDeps "
            "(roadmap step 5.6 wires one); a Virtual placement cannot be acquired without it."
        )
    await _authorize_egress(deps, task, placement)
    try:
        link = await deps.virtual_provider.acquire(placement, task)
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
        inventory = await build_inventory(deps, wardens, virtual_backends=zeroed)
    else:
        inventory = await build_inventory(
            deps, wardens, exclude_dormant=frozenset({placement.cell_id})
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
    link = await deps.virtual_provider.acquire(retry, task)  # A second failure propagates.
    return link, retry


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
