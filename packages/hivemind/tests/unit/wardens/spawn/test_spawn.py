"""Tests for hivemind.wardens.spawn.spawn: spawn_sub_bee builds a context within the ceiling.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/spawn.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.spawn for the module under test.
    - .claude/codingrules.md section 15 for "capabilities only attenuate down the tree".
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from builders.cells import make_cell
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import CellKind
from hivemind.cell.lease import LeaseRequest
from hivemind.cell.tiers import AccessLevel
from hivemind.guard.access import ceiling_for
from hivemind.wardens.spawn import WardenCellContext, spawn_sub_bee, stop_sub_bee
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages import PlannedLeaving
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.task import TaskAssign, WorkerRole


def _grant(active_clock: Clock, grant_id: GrantId) -> GrantIssued:
    return GrantIssued(
        grant_id=grant_id,
        holder=new_warden_id(active_clock),
        cell_id=new_cell_id(active_clock),
        task_id=None,
        revision=0,
        allowed=(
            AllowedBinding(
                slot="WORKER",
                source=SourceRef(
                    source_id="local", provider="fake", model="test-model", host_cell_id=None
                ),
                max_effort=WireEffort.MEDIUM,
            ),
        ),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=active_clock.now(),
        reason="test grant",
    )


def _capturing_worker_factory(
    captured: dict[str, object],
) -> Callable[[WorkerRole], ScriptedWorker]:
    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        captured["capabilities"] = ctx.capabilities
        await asyncio.sleep(0)
        return make_outcome()

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


async def test_spawn_sub_bee_never_grants_capabilities_wider_than_the_ceiling() -> None:
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.SCRATCH)
    captured: dict[str, object] = {}
    deps, _queen_end, warden_id = make_warden_deps(
        cells=(cell,), worker_factory=_capturing_worker_factory(captured)
    )
    lease = await deps.source.lease(
        LeaseRequest(
            cell_id=cell.id, holder=warden_id, task_id=None, access_level=cell.access_level
        )
    )
    session = await deps.source.open_session(lease)
    ceiling = ceiling_for(lease.access_level, lease.scratch_root)
    assignment = make_assignment(clock=deps.clock)
    grant = _grant(deps.clock, assignment.grant_id)
    ctx = WardenCellContext(
        warden_id=warden_id, deps=deps, ceiling=ceiling, cell=cell, lease=lease, session=session
    )

    sub_bee = await spawn_sub_bee(ctx, assignment, grant)
    for _ in range(10):
        await asyncio.sleep(0)
        if "capabilities" in captured:
            break

    granted = captured["capabilities"]
    assert granted.issubset(ceiling)  # type: ignore[attr-defined]
    # SCRATCH never grants net/device: proves the slice is strictly narrower, not merely equal.
    assert not any(cap.family.value == "net" for cap in granted.capabilities)  # type: ignore[attr-defined]

    await stop_sub_bee(sub_bee, deps.clock)
    await sub_bee.link.close()


def _capturing_capping_worker_factory(
    captured: dict[str, object],
) -> Callable[[WorkerRole], ScriptedWorker]:
    """Build a worker_factory whose script records `ctx.capping`'s own GateDeps (test-only)."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        captured["capping"] = ctx.capping
        await asyncio.sleep(0)
        return make_outcome()

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


async def test_spawn_sub_bee_threads_assignment_leaves_into_the_capping_gate() -> None:
    """Roadmap step 5.0c: TaskAssign.leaves reaches this sub-bee's own GateDeps.declared_leaves."""
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.FULL)
    captured: dict[str, object] = {}
    deps, _queen_end, warden_id = make_warden_deps(
        cells=(cell,), worker_factory=_capturing_capping_worker_factory(captured)
    )
    lease = await deps.source.lease(
        LeaseRequest(
            cell_id=cell.id, holder=warden_id, task_id=None, access_level=cell.access_level
        )
    )
    session = await deps.source.open_session(lease)
    ceiling = ceiling_for(lease.access_level, lease.scratch_root)
    leaves = (PlannedLeaving(pattern="~/keep.txt", reason="the goal asked for it"),)
    assignment = make_assignment(clock=deps.clock, leaves=leaves)
    grant = _grant(deps.clock, assignment.grant_id)
    ctx = WardenCellContext(
        warden_id=warden_id, deps=deps, ceiling=ceiling, cell=cell, lease=lease, session=session
    )

    sub_bee = await spawn_sub_bee(ctx, assignment, grant)
    for _ in range(10):
        await asyncio.sleep(0)
        if "capping" in captured:
            break

    gate = captured["capping"]
    assert gate._deps.declared_leaves == leaves  # type: ignore[attr-defined]
    # The same gate hands the real-time judge this sub-bee's own objective to judge against.
    assert gate._deps.goal == assignment.objective  # type: ignore[attr-defined]

    await stop_sub_bee(sub_bee, deps.clock)
    await sub_bee.link.close()


async def test_spawn_sub_bee_widens_lease_reachability_under_full_access(tmp_path: Path) -> None:
    """Roadmap step 5.0e: keep_root and a declared leaving's root become reachable under FULL."""
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.FULL)
    keep_root = tmp_path / "keep"
    deps, _queen_end, warden_id = make_warden_deps(cells=(cell,), keep_root=keep_root)
    lease = await deps.source.lease(
        LeaseRequest(
            cell_id=cell.id, holder=warden_id, task_id=None, access_level=cell.access_level
        )
    )
    session = await deps.source.open_session(lease)
    ceiling = ceiling_for(lease.access_level, lease.scratch_root)
    leaves = (PlannedLeaving(pattern="~/Projects/app", reason="the goal asked for it"),)
    assignment = make_assignment(clock=deps.clock, leaves=leaves)
    grant = _grant(deps.clock, assignment.grant_id)
    ctx = WardenCellContext(
        warden_id=warden_id, deps=deps, ceiling=ceiling, cell=cell, lease=lease, session=session
    )

    sub_bee = await spawn_sub_bee(ctx, assignment, grant)

    assert lease.is_path_allowed(keep_root / "kept.txt")
    assert lease.is_path_allowed(deps.leave_home / "Projects" / "app" / "file.py")

    await stop_sub_bee(sub_bee, deps.clock)
    await sub_bee.link.close()


async def test_spawn_sub_bee_never_widens_lease_reachability_under_scratch_access() -> None:
    """Under SCRATCH the leave policy always DENYs anyway; nothing should widen reachability."""
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.SCRATCH)
    deps, _queen_end, warden_id = make_warden_deps(cells=(cell,), keep_root=Path("/keep"))
    lease = await deps.source.lease(
        LeaseRequest(
            cell_id=cell.id, holder=warden_id, task_id=None, access_level=cell.access_level
        )
    )
    session = await deps.source.open_session(lease)
    ceiling = ceiling_for(lease.access_level, lease.scratch_root)
    leaves = (PlannedLeaving(pattern="~/Projects/app", reason="the goal asked for it"),)
    assignment = make_assignment(clock=deps.clock, leaves=leaves)
    grant = _grant(deps.clock, assignment.grant_id)
    ctx = WardenCellContext(
        warden_id=warden_id, deps=deps, ceiling=ceiling, cell=cell, lease=lease, session=session
    )

    sub_bee = await spawn_sub_bee(ctx, assignment, grant)

    assert lease.allowed_paths == ()

    await stop_sub_bee(sub_bee, deps.clock)
    await sub_bee.link.close()
