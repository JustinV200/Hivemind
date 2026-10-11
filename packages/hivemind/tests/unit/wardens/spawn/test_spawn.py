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
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from builders.cells import make_cell
from builders.routing import make_chain_source
from builders.warden_routing import routing_overrides
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import CellKind
from hivemind.cell.lease import LeaseRequest
from hivemind.cell.tiers import AccessLevel
from hivemind.forage import Abundance, Effort, ModelSlot
from hivemind.guard import warden_set
from hivemind.llm.routing import RoutingRules
from hivemind.llm.slots import BoundModel
from hivemind.pheromone import TrailQuery
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.errors import BindingRefusedError
from hivemind.wardens.spawn import SubBee, WardenCellContext, spawn_sub_bee, stop_sub_bee
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock, FakeClock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages import PlannedLeaving
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.labels import AccuracyBar, Tempo
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
    ceiling = warden_set(deps.guard, lease.access_level, lease.scratch_root)
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
    # Roadmap step 10.2: the slice starts from the Drone's policy default, scratch root filled.
    strings = granted.as_strings()  # type: ignore[attr-defined]
    assert f"fs:write:{lease.scratch_root.as_posix()}/**" in strings
    assert "llm:worker" in strings

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
    ceiling = warden_set(deps.guard, lease.access_level, lease.scratch_root)
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
    ceiling = warden_set(deps.guard, lease.access_level, lease.scratch_root)
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
    ceiling = warden_set(deps.guard, lease.access_level, lease.scratch_root)
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


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 8.1: the first binding is routed by tempo and Forage
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Spawned:
    """A routed spawn's results: the sub-bee, its deps and Queen end, and the model it ran with."""

    sub_bee: SubBee
    deps: WardenDeps
    queen_end: QueenEnd
    bound: BoundModel


def _small_and_strong(clock: FakeClock, **overrides: object) -> dict[str, Any]:
    """A grade-1 primary (`small`) allowed by `[llm.floors]`, falling back to a grade-4 `strong`."""
    sources = [make_chain_source("small", grade=1), make_chain_source("strong", grade=4)]
    rules = RoutingRules(floors={ModelSlot.WORKER: 1})
    return {**routing_overrides(clock, sources, rules=rules), **overrides}


@asynccontextmanager
async def _routed_spawn(
    wiring: dict[str, Any],
    clock: FakeClock,
    accuracy: AccuracyBar,
    binding_override: str | None = None,
) -> AsyncIterator[_Spawned]:
    """Spawn one worker task at `accuracy` over `wiring`; yield it, then retire it."""
    seen: dict[str, object] = {}

    async def script(ctx: WorkerContext, assignment: TaskAssign, resume: object) -> WorkerOutcome:
        seen["bound"] = ctx.bound
        await asyncio.Event().wait()  # Stay alive until the test retires the sub-bee.
        raise AssertionError("unreachable: the test retires this sub-bee first")

    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.SCRATCH, clock=clock)
    deps, queen_end, warden_id = make_warden_deps(
        clock,
        cells=(cell,),
        worker_factory=lambda role: ScriptedWorker(script, role=role),
        **wiring,
    )
    lease = await deps.source.lease(
        LeaseRequest(
            cell_id=cell.id, holder=warden_id, task_id=None, access_level=cell.access_level
        )
    )
    session = await deps.source.open_session(lease)
    ceiling = warden_set(deps.guard, lease.access_level, lease.scratch_root)
    ctx = WardenCellContext(
        warden_id=warden_id, deps=deps, ceiling=ceiling, cell=cell, lease=lease, session=session
    )
    assignment = make_assignment(clock=clock, tempo=Tempo(latency_budget_s=None, accuracy=accuracy))
    sub_bee = await spawn_sub_bee(
        ctx, assignment, _grant(clock, assignment.grant_id), binding_override
    )
    try:
        for _ in range(20):
            await asyncio.sleep(0)
            if "bound" in seen:
                break
        bound = seen["bound"]
        assert isinstance(bound, BoundModel)
        yield _Spawned(sub_bee, deps, queen_end, bound)
    finally:
        await stop_sub_bee(sub_bee, clock)
        await sub_bee.link.close()


async def test_a_critical_task_is_bound_along_the_routed_order_and_records_llm_fallback() -> None:
    clock = FakeClock()
    async with _routed_spawn(_small_and_strong(clock), clock, AccuracyBar.CRITICAL) as spawned:
        # The grade-1 primary is passed over for the grade-4 fallback, which now leads the chain.
        assert spawned.sub_bee.binding == "strong"
        assert [link.binding for link in spawned.bound.chain()] == ["strong", "worker"]
        assert spawned.bound.effort is Effort.HIGH  # CRITICAL: the strongest thinking.
        [event] = await spawned.deps.trail.query(TrailQuery(kind="llm.fallback"))
        assert (event.payload["from_key"], event.payload["to_key"]) == ("worker", "strong")


async def test_a_low_tempo_task_keeps_the_primary_and_records_no_event() -> None:
    clock = FakeClock()
    async with _routed_spawn(_small_and_strong(clock), clock, AccuracyBar.LOW) as spawned:
        assert spawned.sub_bee.binding == "worker"
        assert [link.binding for link in spawned.bound.chain()] == ["worker", "strong"]
        assert spawned.bound.effort is Effort.LOW
        assert await spawned.deps.trail.query(TrailQuery(kind="llm.fallback")) == ()


@pytest.mark.parametrize("unwired", ["map", "routing_rules"])
async def test_without_a_map_or_rules_the_slots_own_key_is_bound_and_nothing_is_routed(
    unwired: str,
) -> None:
    clock = FakeClock()
    wiring = _small_and_strong(clock, **{unwired: None})
    async with _routed_spawn(wiring, clock, AccuracyBar.CRITICAL) as spawned:
        assert spawned.sub_bee.binding == "worker"
        assert spawned.bound.effort is Effort.MEDIUM  # The row's own, untouched by tempo.
        assert await spawned.deps.trail.query(TrailQuery(kind="llm.fallback")) == ()


async def test_an_explicit_binding_override_is_never_routed() -> None:
    clock = FakeClock()
    async with _routed_spawn(_small_and_strong(clock), clock, AccuracyBar.CRITICAL, "worker") as s:
        assert s.sub_bee.binding == "worker"
        assert await s.deps.trail.query(TrailQuery(kind="llm.fallback")) == ()


async def test_the_guard_checks_the_routed_key_not_the_primary() -> None:
    clock = FakeClock()
    # `judge` is the JUDGE slot's own key: a Drone holds no `llm:judge`, so it is refused.
    sources = [make_chain_source("small", grade=1), make_chain_source("judge", grade=4)]
    rules = RoutingRules(floors={ModelSlot.WORKER: 1})
    wiring = routing_overrides(clock, sources, keys=("small", "judge"), rules=rules)

    with pytest.raises(BindingRefusedError) as refused:
        async with _routed_spawn(wiring, clock, AccuracyBar.CRITICAL):
            raise AssertionError("the spawn should have been refused")

    assert refused.value.binding_key == "judge"


async def test_a_seat_shortfall_files_one_forage_request_and_the_spawn_goes_ahead() -> None:
    clock = FakeClock()
    starved = make_chain_source("small", grade=3, abundance=Abundance(seats_free=0))
    wiring = routing_overrides(clock, [starved], keys=("small",))
    async with _routed_spawn(wiring, clock, AccuracyBar.NORMAL) as spawned:
        assert spawned.sub_bee.binding == "worker"  # Not blocked on the Queen's reply.
        await spawned.queen_end.pump_until(lambda: bool(spawned.queen_end.forage_requests))
        [request] = spawned.queen_end.forage_requests
        assert request.wanted.source_id == "src_small"
