"""Tests for the Warden's resume gate: nothing resumes from a Handoff its own store labels tainted.

Roadmap step 10.6a (ADR-0043). Once an isolation's order has tainted a Handoff in the Cell's own
store, the Queen's resume from it (a fresh grant, then a `TaskAssign` resuming from the Handoff,
as after the human's lift) is refused before anything spawns: a `guard.denied` row at the
`isolation` point, the grant withdrawn, and the Queen told the task is held. A Handoff that was
never tainted, or that a judge has since cleared, is admitted and the bee resumes from it, and a
fresh start is admitted whatever the store holds. The Warden's own respawns (a RETRY, a REBIND,
the Queen's ordered rebind, a context Handoff's successor) resume from the bee's last Handoff too,
so they pass the same gate: a tainted one is refused and the task held, with no bee spawned.

Fits into the Hive:
    Mirrors src/hivemind/wardens/isolation/gate.py (codingrules section 3), over a real Warden
    running one held sub-bee (builders.quarantine's scene).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.isolation.gate, under test.
    - tests/unit/wardens/quarantine/test_gate.py for the quarantine's own way out.
"""

from __future__ import annotations

import asyncio
from typing import cast

from builders.memory import make_handoff
from builders.quarantine import (
    QuarantineScene,
    make_grant_issued,
    queen_hears,
    start_scene,
    stop_scene,
    wait_for_event,
)
from builders.taint import make_taint_judge

from hivemind.guard import QUEEN_ROLE, queen_principal, role_set
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.memory.taint import (
    ClearOutcome,
    TaintClearDeps,
    TaintClearRequest,
    TaintedKind,
    TaintTarget,
    clear_taint,
)
from hivemind.pheromone import TrailQuery
from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.alarms import (
    handle_alarm_action,
    rebind_sub_bee,
    resume_from_handoff,
)
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_event_id, new_grant_id
from waggle.messages import AlarmSeverity, HandoffRef
from waggle.messages.cell import CellTaintOrder
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import TaskAssign, TaskStage

_WAIT_S = 5.0  # Bound on waiting for a resumed bee's role to start.


async def _tainted_checkpoint(scene: QuarantineScene) -> HandoffRef:
    """Checkpoint a Handoff for the scene's task, then deliver the isolation's order over it."""
    deps = scene.deps
    suspect_at = deps.clock.now()
    _fake(scene).advance(1.0)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    task = scene.assignment.task_id
    ref = await write_checkpoint(make_handoff(task_id=task), task, ctx)
    cell = scene.warden._cell
    assert cell is not None
    order = CellTaintOrder(
        cell_id=cell.id,
        cause_event_id=new_event_id(deps.clock),
        suspect_at=suspect_at,
        authors=(scene.warden._warden_id,),
        task_ids=(task,),
        reason="Cell isolated (queen) on a Guard report.",
    )
    await scene.queen.send(order)
    await wait_for_event(deps.trail, "memory.tainted", ref.event_id)
    return ref


async def _finish_first_attempt(scene: QuarantineScene) -> None:
    """Let the held first attempt claim, so the task's next assignment spawns a fresh bee."""
    scene.role.started.clear()
    scene.role.finish.set()
    await queen_hears(scene, lambda: bool(scene.queen.results))


async def _resume(scene: QuarantineScene, resume_from: HandoffRef | None) -> TaskAssign:
    """Send the Queen's resume: a fresh grant, then attempt 2 resuming from `resume_from`."""
    grant_id = new_grant_id(scene.deps.clock)
    assignment = scene.assignment.model_copy(
        update={"grant_id": grant_id, "attempt": 2, "resume_from": resume_from}
    )
    await scene.queen.send(make_grant_issued(scene.deps.clock, assignment))
    await scene.queen.send(assignment)
    return assignment


async def _refusals(scene: QuarantineScene) -> list[tuple[str, str]]:
    """The point and rule of every `guard.denied` row on the Warden's trail."""
    denied = await scene.deps.trail.query(TrailQuery(kind="guard.denied"))
    return [(str(event.payload["point"]), str(event.payload["rule"])) for event in denied]


async def test_a_resume_from_a_tainted_handoff_is_refused_and_the_task_held() -> None:
    scene = await start_scene()
    ref = await _tainted_checkpoint(scene)
    await _finish_first_attempt(scene)

    assignment = await _resume(scene, ref)
    await queen_hears(scene, lambda: bool(scene.queen.progress))

    assert await _refusals(scene) == [("isolation", "guard.scope.tainted_handoff")]
    [held] = scene.queen.progress
    assert (held.task_id, held.stage) == (assignment.task_id, TaskStage.PAUSED)
    assert assignment.grant_id not in scene.warden._grants  # Nothing spawns under it.
    assert scene.role.resumed_from == [None]  # Only the first attempt ever ran.
    await stop_scene(scene)


async def test_a_resume_from_an_untainted_handoff_is_admitted() -> None:
    scene = await start_scene()
    deps = scene.deps
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    task = scene.assignment.task_id
    ref = await write_checkpoint(make_handoff(task_id=task), task, ctx)
    await _finish_first_attempt(scene)

    await _resume(scene, ref)
    await asyncio.wait_for(scene.role.started.wait(), timeout=_WAIT_S)

    assert await _refusals(scene) == []
    assert scene.role.resumed_from[-1] is not None
    await stop_scene(scene)


async def test_a_handoff_a_judge_cleared_is_admitted_again() -> None:
    scene = await start_scene()
    ref = await _tainted_checkpoint(scene)
    deps = scene.deps
    judge, _provider = make_taint_judge("CLEAR")
    request = TaintClearRequest(
        target=TaintTarget(kind=TaintedKind.HANDOFF, item_id=ref.event_id),
        clearer=queen_principal(deps.identity.hive_id),
        held=role_set(deps.guard, QUEEN_ROLE),
    )
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    clear_deps = TaintClearDeps(judge=judge, enforcer=deps.enforcer, ctx=ctx)
    cleared = await clear_taint(request, clear_deps)
    await _finish_first_attempt(scene)

    await _resume(scene, ref)
    await asyncio.wait_for(scene.role.started.wait(), timeout=_WAIT_S)

    assert cleared.outcome is ClearOutcome.CLEARED
    assert await _refusals(scene) == []
    assert scene.role.resumed_from[-1] is not None
    await stop_scene(scene)


async def test_a_fresh_start_is_admitted_whatever_the_store_holds() -> None:
    scene = await start_scene()
    await _tainted_checkpoint(scene)
    await _finish_first_attempt(scene)

    await _resume(scene, None)
    await asyncio.wait_for(scene.role.started.wait(), timeout=_WAIT_S)

    assert await _refusals(scene) == []
    assert scene.role.resumed_from[-1] is None
    await stop_scene(scene)


async def test_a_wardens_own_retry_from_a_tainted_handoff_is_refused_and_the_task_held() -> None:
    scene = await start_scene()
    sub_bee = await _bee_at_tainted_handoff(scene)

    await handle_alarm_action(scene.warden, sub_bee, _alarm(scene), WardenAction.RETRY, None)

    await _assert_held_without_a_respawn(scene)


async def test_a_wardens_own_rebind_from_a_tainted_handoff_is_refused_and_the_task_held() -> None:
    scene = await start_scene()
    sub_bee = await _bee_at_tainted_handoff(scene)

    await handle_alarm_action(
        scene.warden, sub_bee, _alarm(scene), WardenAction.REBIND, sub_bee.binding
    )

    await _assert_held_without_a_respawn(scene)


async def test_a_queen_ordered_rebind_from_a_tainted_handoff_is_refused() -> None:
    scene = await start_scene()
    sub_bee = await _bee_at_tainted_handoff(scene)

    await rebind_sub_bee(scene.warden, sub_bee, sub_bee.binding)

    await _assert_held_without_a_respawn(scene)


async def test_a_context_handoffs_successor_from_a_tainted_handoff_is_refused() -> None:
    scene = await start_scene()
    sub_bee = await _bee_at_tainted_handoff(scene)

    await resume_from_handoff(scene.warden, sub_bee)

    await _assert_held_without_a_respawn(scene)


async def test_a_wardens_own_retry_from_an_untainted_handoff_respawns() -> None:
    scene = await start_scene()
    deps = scene.deps
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    task = scene.assignment.task_id
    [sub_bee] = scene.warden._sub_bees.values()
    sub_bee.last_handoff = await write_checkpoint(make_handoff(task_id=task), task, ctx)
    scene.role.started.clear()

    await handle_alarm_action(scene.warden, sub_bee, _alarm(scene), WardenAction.RETRY, None)
    await asyncio.wait_for(scene.role.started.wait(), timeout=_WAIT_S)

    assert await _refusals(scene) == []
    assert scene.role.resumed_from[-1] is not None
    await stop_scene(scene)


async def _bee_at_tainted_handoff(scene: QuarantineScene) -> SubBee:
    """Return the scene's running bee, its last Handoff one the isolation's order tainted."""
    ref = await _tainted_checkpoint(scene)
    [sub_bee] = scene.warden._sub_bees.values()
    # As if the bee had reported the checkpoint: a respawn resumes from exactly this Handoff.
    sub_bee.last_handoff = ref
    return sub_bee


async def _assert_held_without_a_respawn(scene: QuarantineScene) -> None:
    """Assert the gate refused the respawn: its row, the task held, and no fresh bee running."""
    await queen_hears(scene, lambda: bool(scene.queen.progress))
    assert await _refusals(scene) == [("isolation", "guard.scope.tainted_handoff")]
    [held] = scene.queen.progress
    assert (held.task_id, held.stage) == (scene.assignment.task_id, TaskStage.PAUSED)
    assert scene.assignment.grant_id not in scene.warden._grants  # Nothing spawns under it.
    assert scene.warden._sub_bees == {}
    assert scene.role.resumed_from == [None]  # Only the first attempt ever ran.
    await stop_scene(scene)


def _alarm(scene: QuarantineScene) -> AlarmRaised:
    """A sub-bee's own WORKER_FAILED Alarm about the scene's task, as its Warden would receive."""
    clock = scene.deps.clock
    [sub_bee] = scene.warden._sub_bees.values()
    return AlarmRaised(
        alarm_id=new_alarm_id(clock),
        kind=AlarmKind.WORKER_FAILED,
        severity=AlarmSeverity.WARNING,
        origin=sub_bee.worker_id,
        attempts=0,
        raised_at=clock.now(),
        context=AlarmContext(
            task_id=scene.assignment.task_id,
            cell_id=None,
            worker_id=sub_bee.worker_id,
            event_id=None,
            handoff=None,
        ),
        detail="The tool call raised an exception.",
        clearance=WireHoneyClearance.C1,
        reason="The bee failed; its Warden retries it.",
    )


def _fake(scene: QuarantineScene) -> FakeClock:
    """The scene's clock, which is always a FakeClock (builders.quarantine.start_scene)."""
    return cast(FakeClock, scene.deps.clock)
