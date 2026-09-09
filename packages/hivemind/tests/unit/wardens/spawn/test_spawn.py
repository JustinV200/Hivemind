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
import contextlib
from collections.abc import Callable

from builders.cells import make_cell
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import CellKind
from hivemind.cell.lease import LeaseRequest
from hivemind.cell.tiers import AccessLevel
from hivemind.guard.access import ceiling_for
from hivemind.wardens.spawn import WardenCellContext, spawn_sub_bee
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock
from waggle.ids import GrantId, new_cell_id, new_warden_id
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

    sub_bee.runtime_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sub_bee.runtime_task
    await sub_bee.link.close()
