"""Tests for hivemind.wardens.spawn.equip: a sub-bee gets exactly the Exoskeleton its task needs.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/equip.py (codingrules section 3), driven through
    spawn_sub_bee and stop_sub_bee, the only two callers.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.equip for the module under test.
    - .claude/roadmap.md step 6.4: "A test asserts no display process exists after a
      terminal-only task."
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from builders.cells import make_capabilities, make_cell
from builders.exoskeleton import desktop_session
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import CellKind, HoneyClearance
from hivemind.cell.lease import LeaseRequest
from hivemind.cell.tiers import AccessLevel
from hivemind.exoskeleton import AttachError, ExoskeletonHandle
from hivemind.exoskeleton.recorder import InMemoryRecordingStore
from hivemind.guard.access import ceiling_for
from hivemind.memory import BeeBread, BeeBreadEntryKind
from hivemind.pheromone.trail.protocol import TrailQuery
from hivemind.wardens.spawn import WardenCellContext, spawn_sub_bee, stop_sub_bee
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.task import ExoskeletonNeed, TaskAssign, WorkerRole

_DESKTOP = make_capabilities(can_start_display=True, has_audio=True)


def _grant(clock: Clock, grant_id: GrantId) -> GrantIssued:
    source = SourceRef(source_id="local", provider="fake", model="test-model", host_cell_id=None)
    return GrantIssued(
        grant_id=grant_id,
        holder=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        task_id=None,
        revision=0,
        allowed=(AllowedBinding(slot="WORKER", source=source, max_effort=WireEffort.MEDIUM),),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=clock.now(),
        reason="test grant",
    )


def _recording(seen: dict[str, ExoskeletonHandle | None]) -> Callable[[WorkerRole], ScriptedWorker]:
    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        seen["exoskeleton"] = ctx.exoskeleton  # What the role's tools would drive.
        await asyncio.sleep(0)
        return make_outcome()

    return lambda role: ScriptedWorker(script, role=role)


async def _context(
    seen: dict[str, ExoskeletonHandle | None],
    capabilities: object = _DESKTOP,
    recording_store: InMemoryRecordingStore | None = None,
) -> tuple[WardenCellContext, Clock]:
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.FULL, capabilities=capabilities)
    deps, _queen_end, warden_id = make_warden_deps(
        cells=(cell,), worker_factory=_recording(seen), recording_store=recording_store
    )
    request = LeaseRequest(
        cell_id=cell.id, holder=warden_id, task_id=None, access_level=cell.access_level
    )
    lease = await deps.source.lease(request)
    # A session that answers attach's commands the way a desktop Cell does.
    session = desktop_session(lease.scratch_root, deps.clock)
    ceiling = ceiling_for(lease.access_level, lease.scratch_root)
    ctx = WardenCellContext(
        warden_id=warden_id, deps=deps, ceiling=ceiling, cell=cell, lease=lease, session=session
    )
    return ctx, deps.clock


async def _settle(seen: dict[str, ExoskeletonHandle | None]) -> None:
    for _ in range(20):
        await asyncio.sleep(0)
        if "exoskeleton" in seen:
            return


async def test_a_terminal_only_task_never_starts_a_display_process() -> None:
    seen: dict[str, ExoskeletonHandle | None] = {}
    ctx, clock = await _context(seen)
    assignment = make_assignment(clock=clock)

    sub_bee = await spawn_sub_bee(ctx, assignment, _grant(clock, assignment.grant_id))
    await _settle(seen)
    await stop_sub_bee(sub_bee, clock)
    await sub_bee.link.close()

    assert seen["exoskeleton"] is None
    assert sub_bee.exoskeleton is None
    assert ctx.session.started == ()  # type: ignore[attr-defined]  # No Xvfb, no anything.


async def test_an_exoskeleton_task_is_equipped_before_its_role_and_unequipped_when_it_stops() -> (
    None
):
    seen: dict[str, ExoskeletonHandle | None] = {}
    ctx, clock = await _context(seen)
    assignment = make_assignment(clock=clock, exoskeleton=ExoskeletonNeed(audio=True))

    sub_bee = await spawn_sub_bee(ctx, assignment, _grant(clock, assignment.grant_id))
    await _settle(seen)
    handle = seen["exoskeleton"]
    assert handle is not None and handle is sub_bee.exoskeleton
    assert handle.plan.peripherals() == ("compound_eye", "antennae", "buzz")

    await stop_sub_bee(sub_bee, clock)
    await sub_bee.link.close()

    assert handle.detached
    assert ctx.session.running_pids == ()  # type: ignore[attr-defined]
    kinds = [event.kind for event in await ctx.deps.trail.query(TrailQuery())]
    assert kinds.count("cell.exoskeleton_attached") == kinds.count("cell.exoskeleton_detached") == 1


async def test_a_cell_that_cannot_equip_the_task_refuses_before_any_runtime_starts() -> None:
    seen: dict[str, ExoskeletonHandle | None] = {}
    ctx, clock = await _context(seen, capabilities=make_capabilities())  # Terminal-only Cell.
    assignment = make_assignment(clock=clock, exoskeleton=ExoskeletonNeed())

    with pytest.raises(AttachError, match="neither start a display"):
        await spawn_sub_bee(ctx, assignment, _grant(clock, assignment.grant_id))

    assert seen == {}  # The role never ran.
    assert ctx.session.started == ()  # type: ignore[attr-defined]


async def test_the_sub_bees_gate_applies_gui_through_a_recorded_surface() -> None:
    seen: dict[str, ExoskeletonHandle | None] = {}
    store = InMemoryRecordingStore()
    ctx, clock = await _context(seen, recording_store=store)
    assignment = make_assignment(clock=clock, exoskeleton=ExoskeletonNeed())

    sub_bee = await spawn_sub_bee(ctx, assignment, _grant(clock, assignment.grant_id))
    await _settle(seen)
    await stop_sub_bee(sub_bee, clock)
    await sub_bee.link.close()

    (recording,) = await store.recordings()
    assert recording.task_id == str(assignment.task_id)
    entries = await BeeBread(ctx.deps.memory).by_task(assignment.task_id, HoneyClearance.C2)
    assert [entry.ref_ids for entry in entries if entry.kind is BeeBreadEntryKind.RECORDING] == [
        (recording.recording_id,)
    ]
