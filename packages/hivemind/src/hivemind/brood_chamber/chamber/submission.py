"""Define _SubmissionMixin: BroodChamber.submit, minting a whole task graph at once.

Submitting is its own concept, distinct from advancing an existing task (`lifecycle.py`,
`outcomes.py`, `questions.py`) or reading one back (`queries.py`): it mints every `TaskId` a
`TaskGraphDraft` (`hivemind.brood_chamber.task.model`, the JSON graph file `hive tasks submit`
reads) needs before any `Task` exists, resolves each draft's `depends_on` keys to those ids, and
inserts every minted `Task` with its `task.submitted` `TaskEvent` in one atomic
`TaskStore.insert_tasks` call.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `BroodChamber`
    (`hivemind.brood_chamber.chamber`); not imported anywhere else. Calls into
    `hivemind.brood_chamber.task.model` and `hivemind.brood_chamber.chamber.base` only.

Key invariants:
    - The first draft in `graph.tasks` mints the goal: every Task this mixin builds gets
      `goal_id` equal to that first minted id, per `TaskGraphDraft`'s own documented contract.
    - Every minted Task's `depends_on` is resolved from draft keys to the ids minted in this same
      call, never to an id from an earlier submission.

See Also:
    - hivemind.brood_chamber.task.model for TaskGraphDraft, TaskDraft and TaskSpec.
    - hivemind.brood_chamber.chamber.base for _ChamberBase, the shared write helpers this mixin
      uses.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import JsonValue

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.task.model import Task, TaskDraft, TaskGraphDraft, TaskSpec
from hivemind.brood_chamber.task.state import TaskStatus
from hivemind.pheromone import TaskEvent
from waggle.ids import TaskId, new_task_id

# Private mixin (leading underscore): nothing in this file is part of any package's public API,
# so __all__ stays empty rather than listing a name codingrules 5.4 would then call exported.
__all__: list[str] = []


class _SubmissionMixin(_ChamberBase):
    """BroodChamber.submit: mint a whole TaskGraphDraft into Tasks in one atomic write."""

    async def submit(self, graph: TaskGraphDraft) -> tuple[Task, ...]:
        """Mint one Task per draft in `graph` and insert them all atomically.

        Args:
            graph: An already-validated task graph (acyclic, unique keys, known depends_on).
                The first draft becomes the goal; every minted Task's `goal_id` is its id.

        Returns:
            The minted Tasks, in the same order as `graph.tasks`.

        Raises:
            hivemind.brood_chamber.errors.TaskAlreadyExistsError: A minted id already exists,
                which would only happen on an id collision; nothing is written.
        """
        now = self._clock.now()
        # Mint every id up front, in file order, so depends_on can resolve draft keys to real
        # TaskIds before any Task is built; the first minted id is every task's goal_id.
        minted_ids = {draft.key: new_task_id(self._clock) for draft in graph.tasks}
        goal_id = minted_ids[graph.tasks[0].key]

        tasks: list[Task] = []
        events: list[TaskEvent] = []
        for draft in graph.tasks:
            task = _mint_task(draft, minted_ids, goal_id, now)
            tasks.append(task)
            payload: dict[str, JsonValue] = {
                "title": task.spec.title,
                "goal_id": goal_id,
                "depends_on": list(task.spec.depends_on),
            }
            events.append(self._build_event(task.id, "task.submitted", payload, now))

        await self._store.insert_tasks(tasks, events)
        return tuple(tasks)


def _mint_task(
    draft: TaskDraft, minted_ids: Mapping[str, TaskId], goal_id: TaskId, now: datetime
) -> Task:
    """Build one PENDING Task from `draft`, resolving its draft-key dependencies to real TaskIds.

    A module-level function rather than a method: it touches no chamber state at all, and pulling
    it out of `submit` is what keeps that method inside the codingrules 5.1 fifty-line budget once
    `TaskSpec` carries `origin` (roadmap step 5.7a), `leaves` (roadmap step 5.0b), the goal's
    `capabilities` (roadmap step 10.3) and its request's id and spend cap (roadmap step 10.5).

    Args:
        draft: The already-validated draft to mint.
        minted_ids: Every draft key in the graph mapped to the TaskId minted for it.
        goal_id: The graph's own goal id (the first draft's minted id).
        now: The one `created_at`/`updated_at` reading the whole submission shares.

    Returns:
        The minted, PENDING Task.
    """
    return Task(
        id=minted_ids[draft.key],
        goal_id=goal_id,
        spec=TaskSpec(
            title=draft.title,
            objective=draft.objective,
            acceptance=draft.acceptance,
            needs=draft.needs,
            clearance=draft.clearance,
            origin=draft.origin,
            depends_on=tuple(minted_ids[key] for key in draft.depends_on),
            leaves=draft.leaves,
            capabilities=draft.capabilities,
            goal_request_id=draft.goal_request_id,
            spend_cap_usd=draft.spend_cap_usd,
        ),
        status=TaskStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
