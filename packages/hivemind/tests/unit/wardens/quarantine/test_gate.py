"""Tests for the only way out of quarantine: a respawn from the checkpoint a judge has cleared.

ADR-0043: "The only way out is a respawn from a Handoff the judge has cleared." After a
quarantine, the Queen's resume (a fresh grant and a `TaskAssign` resuming from the checkpoint)
is refused while the checkpoint is still tainted, as `read_handoff` refuses it: nothing spawns,
the refusal is a `guard.denied` row at the `quarantine` point, and the Queen is told again that
the task is held. An assignment resuming from nothing, or from any other Handoff, is refused the
same way even once the checkpoint is cleared. Once `clear_taint` has returned CLEARED on a judge's
verdict, the same respawn is admitted, lifts the quarantine, and the new bee resumes from the
cleared checkpoint.

Fits into the Hive:
    Mirrors src/hivemind/wardens/quarantine/gate.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.quarantine.gate for admit_respawn.
    - hivemind.memory.taint.clear for clear_taint, the one clearer.
"""

from __future__ import annotations

import asyncio

from builders.memory import make_handoff
from builders.quarantine import (
    QuarantineScene,
    drain,
    make_grant_issued,
    queen_hears,
    start_scene,
    stop_scene,
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
from waggle.ids import new_event_id, new_grant_id
from waggle.messages import HandoffRef
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskAssign, TaskStage

_WAIT_S = 5.0  # Bound on waiting for a respawned bee's role to start.


async def _quarantine(scene: QuarantineScene) -> HandoffRef:
    """Quarantine the scene's bee on the Queen's order and return its checkpoint."""
    bee = scene.warden.sub_bees[0].worker_id
    order = Intervene(
        action=InterventionAction.QUARANTINE,
        subject=bee,
        task_id=scene.assignment.task_id,
        slot=None,
        alarm_id=None,
        reason="Guard report: injection suspected.",
        suspect_episode_id=new_event_id(scene.deps.clock),
    )
    await scene.queen.send(order)
    await queen_hears(scene, lambda: bool(scene.queen.alarms))
    return scene.warden._quarantined[scene.assignment.task_id].checkpoint


async def _respawn(scene: QuarantineScene, resume_from: HandoffRef | None) -> TaskAssign:
    """Send the Queen's resume: a fresh grant, then the next attempt resuming from `resume_from`."""
    grant_id = new_grant_id(scene.deps.clock)
    assignment = scene.assignment.model_copy(
        update={"grant_id": grant_id, "attempt": 2, "resume_from": resume_from}
    )
    await scene.queen.send(make_grant_issued(scene.deps.clock, assignment))
    await scene.queen.send(assignment)
    return assignment


async def _clear(scene: QuarantineScene, checkpoint: HandoffRef) -> ClearOutcome:
    """Ask the judge (scripted CLEAR) to clear the checkpoint, the Queen as the clearer."""
    deps = scene.deps
    judge, _provider = make_taint_judge("CLEAR")
    request = TaintClearRequest(
        target=TaintTarget(kind=TaintedKind.HANDOFF, item_id=checkpoint.event_id),
        clearer=queen_principal(deps.identity.hive_id),
        held=role_set(deps.guard, QUEEN_ROLE),
    )
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    result = await clear_taint(
        request, TaintClearDeps(judge=judge, enforcer=deps.enforcer, ctx=ctx)
    )
    return result.outcome


async def _refusals(scene: QuarantineScene) -> list[str]:
    """The rule of every `guard.denied` row on the scene's trail."""
    denied = await scene.deps.trail.query(TrailQuery(kind="guard.denied"))
    return [str(event.payload["rule"]) for event in denied]


async def test_a_respawn_from_the_still_tainted_checkpoint_is_refused_and_the_task_held() -> None:
    scene = await start_scene()
    checkpoint = await _quarantine(scene)

    await _respawn(scene, checkpoint)
    await queen_hears(scene, lambda: len(scene.queen.progress) == 2)

    assert await _refusals(scene) == ["guard.scope.quarantine_checkpoint"]
    assert scene.warden.sub_bees == ()
    assert [held.stage for held in scene.queen.progress] == [TaskStage.PAUSED] * 2
    assert scene.assignment.task_id in scene.warden._quarantined
    assert scene.role.resumed_from == [None]  # Only the first, quarantined attempt ever ran.
    await stop_scene(scene)


async def test_a_cleared_checkpoint_lets_the_task_out_and_the_bee_resumes_from_it() -> None:
    scene = await start_scene()
    checkpoint = await _quarantine(scene)
    scene.role.started.clear()

    outcome = await _clear(scene, checkpoint)
    await _respawn(scene, checkpoint)
    await asyncio.wait_for(scene.role.started.wait(), timeout=_WAIT_S)

    assert outcome is ClearOutcome.CLEARED
    assert scene.warden._quarantined == {}
    assert await _refusals(scene) == []
    [bee] = scene.warden.sub_bees
    assert bee.attempt == 2 and bee.assignment.resume_from == checkpoint
    resumed = scene.role.resumed_from[-1]
    assert resumed is not None and "Quarantine checkpoint" in resumed.notes
    await stop_scene(scene)


async def test_no_other_way_out_even_once_the_checkpoint_is_cleared() -> None:
    scene = await start_scene()
    checkpoint = await _quarantine(scene)
    deps = scene.deps
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    task = scene.assignment.task_id
    other = await write_checkpoint(make_handoff(task_id=task), task, ctx)
    assert await _clear(scene, checkpoint) is ClearOutcome.CLEARED

    for resume_from in (None, other):
        await _respawn(scene, resume_from)
        await drain()

    assert await _refusals(scene) == ["guard.scope.quarantine_checkpoint"] * 2
    assert scene.warden.sub_bees == () and task in scene.warden._quarantined
    await stop_scene(scene)
