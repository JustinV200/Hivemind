"""Tests for hivemind.queen.queen.Queen: a Nurse Bee rejection, sent back and then escalated.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_results.py. Exercises hivemind.queen.ticks.nurse through the Queen's own tick
    (ADR-0061).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.nurse for the module under test.
"""

from __future__ import annotations

import asyncio

from builders.queen import make_queen_deps, plan_responder
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.chat import ChatQuery
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.queen import Queen
from hivemind.queen.ticks.nurse import escalate_unresolved
from hivemind.supervision import AlarmKind
from waggle.ids import TaskId, WardenId, new_cell_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import (
    NurseFinding,
    NurseOutcome,
    NurseReview,
    NurseReviewStatus,
    NurseVerdict,
    TaskOutcome,
    TaskResult,
)

_FINDING = NurseFinding(
    path="scratch/poem.txt", line=1, problem="The poem has no title.", fix="Add a title line."
)


def _one_task_plan(goal: str) -> dict[str, object]:
    criterion: dict[str, object] = {
        "kind": "FILE_EXISTS",
        "subject": "scratch/poem.txt",
        "argv": [],
        "expected": None,
    }
    task = {
        "key": "poem",
        "title": "Write a poem",
        "objective": f"Write a poem about: {goal}",
        "acceptance": [criterion],
        "needs": {},
        "clearance": "C1",
        "depends_on": [],
    }
    return {"tasks": [task]}


def _rejected(task_id: TaskId, warden_id: WardenId, attempt: int, round_: int) -> TaskResult:
    """The FAILED result a Warden sends when its Nurse asked for changes."""
    verdict = NurseVerdict(
        outcome=NurseOutcome.CHANGES_REQUESTED, findings=(_FINDING,), rubric_id="nurse-review/1"
    )
    review = NurseReview(
        status=NurseReviewStatus.REVIEWED,
        round=round_,
        verdict=verdict,
        nurse_grade=3,
        worker_grade=3,
    )
    return TaskResult(
        task_id=task_id,
        attempt=attempt,
        outcome=TaskOutcome.FAILED,
        summary="Wrote the poem.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=warden_id,
        handoff=None,
        spend=0.0,
        reason="nurse",
        review=review,
    )


async def test_a_rejection_is_fixed_forward_with_its_findings_then_escalated_at_the_cap() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, nurse_max_rounds=1)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("The sea.", clearance=HoneyClearance.C1)
    first = await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    # Round 1 is rejected: a fresh Worker on the same Cell gets the findings and the count.
    await warden_end.send(_rejected(first.task_id, link.warden_id, attempt=1, round_=1))
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)
    retry = warden_end.assignments[1]
    assert (retry.task_id, retry.attempt, retry.review_round) == (first.task_id, 2, 1)
    assert retry.review_findings == (_FINDING,)
    assert (await deps.chamber.get(first.task_id)).nurse_rounds == 1

    # Round 2 is rejected too, at the cap of 1: the human gets it, and the task is closed out.
    await warden_end.send(_rejected(first.task_id, link.warden_id, attempt=2, round_=2))
    for _ in range(200):
        if (await deps.chamber.get(first.task_id)).status is TaskStatus.FAILED:
            break
        await asyncio.sleep(0)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert (await deps.chamber.get(first.task_id)).status is TaskStatus.FAILED
    assert len(warden_end.assignments) == 2  # Never sent back a second time.
    [alarm] = queen.human_inbox.alarms
    assert alarm.kind is AlarmKind.NURSE_UNRESOLVED and alarm.context.task_id == first.task_id
    note = queen.human_inbox.note_for(alarm.id)
    assert note is not None and _FINDING.problem in note and "<<<nurse_findings>>>" in note
    assert _FINDING.problem not in alarm.detail  # What the trail, chat and awake episodes read.
    # The words reach the inbox alone, and go with it once the human acknowledges the Alarm.
    assert await queen.acknowledge_alarm(alarm.id)
    assert queen.human_inbox.note_for(alarm.id) is None
    rows = await deps.trail.query(TrailQuery(subject_id=alarm.id))
    assert {row.kind for row in rows} == {"alarm.escalated", "alarm.resolved"}
    assert all(_FINDING.problem not in str(row.payload) for row in rows)
    assert all(_FINDING.problem not in line.text for line in await deps.chat.read(ChatQuery()))
    await warden_end.close()


async def test_a_night_veil_tasks_findings_never_reach_the_human_inbox() -> None:
    # Regression: the inbox is not purged at a Night Veil teardown, so the Cell's words stay out.
    deps, link, warden_end = make_queen_deps()
    (task,) = await deps.chamber.submit(make_graph_draft({"poem": ()}))
    await deps.chamber.assign(
        task.id,
        link.warden_id,
        new_cell_id(deps.clock),
        "Placed.",
        bound_tier=CombShieldLevel.NIGHT_VEIL,
    )
    await deps.chamber.start(task.id)
    inbox = HumanInbox()

    await escalate_unresolved(deps, inbox, _rejected(task.id, link.warden_id, 1, 1), link.warden_id)

    [alarm] = inbox.alarms
    assert alarm.kind is AlarmKind.NURSE_UNRESOLVED and inbox.note_for(alarm.id) is None
    assert (await deps.chamber.get(task.id)).status is TaskStatus.FAILED
    await warden_end.close()
