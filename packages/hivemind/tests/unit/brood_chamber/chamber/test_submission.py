"""Tests for hivemind.brood_chamber.chamber.submission: BroodChamber.submit.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/chamber/submission.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one). Split out of the former test_chamber.py, which exercised the whole
    BroodChamber facade in one file; this file keeps only the tests that exercise
    `_SubmissionMixin.submit`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.chamber.submission for the module under test.
    - .claude/roadmap.md phase 2 step 2.8 for the facade's contract.
"""

from __future__ import annotations

import json

from builders.tasks import make_graph_draft

from hivemind.brood_chamber.chamber import BroodChamber, ChamberIdentity
from hivemind.brood_chamber.store.memory import MemoryTaskStore
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import PheromoneEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id


def _make_chamber(clock: FakeClock) -> tuple[BroodChamber, MemoryTaskStore, MemoryPheromoneTrail]:
    """Build a BroodChamber over a fresh MemoryTaskStore/MemoryPheromoneTrail pair."""
    trail = MemoryPheromoneTrail(clock)
    store = MemoryTaskStore(trail)
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return BroodChamber(store, clock, identity), store, trail


async def _events_for(trail: MemoryPheromoneTrail, subject_id: str) -> tuple[PheromoneEvent, ...]:
    """Return every trail event recorded for `subject_id`, in trail order."""
    return await trail.query(TrailQuery(subject_id=subject_id))


async def test_submit_mints_tasks_in_graph_order_sharing_one_goal_id() -> None:
    clock = FakeClock()
    chamber, _store, _trail = _make_chamber(clock)
    draft = make_graph_draft({"plan": (), "build": ("plan",)})

    tasks = await chamber.submit(draft)

    assert [t.status for t in tasks] == [TaskStatus.PENDING, TaskStatus.PENDING]
    assert tasks[0].goal_id == tasks[0].id
    assert tasks[1].goal_id == tasks[0].id
    assert tasks[1].spec.depends_on == (tasks[0].id,)


async def test_submit_records_one_task_submitted_event_per_task_without_the_objective() -> None:
    clock = FakeClock()
    chamber, _store, trail = _make_chamber(clock)
    sensitive_objective = "do not leak this objective text onto the trail"
    draft = make_graph_draft({"plan": ()})
    draft = draft.model_copy(
        update={"tasks": (draft.tasks[0].model_copy(update={"objective": sensitive_objective}),)}
    )

    (task,) = await chamber.submit(draft)

    events = await _events_for(trail, task.id)
    assert [e.kind for e in events] == ["task.submitted"]
    assert set(events[0].payload) == {"title", "goal_id", "depends_on"}
    assert sensitive_objective not in json.dumps(events[0].payload)
