"""Define _NightVeilMixin: what BroodChamber gives the Night Veil teardown purge.

Codingrules section 12: a Night Veil Cell's work leaves nothing but its skeleton, and the Brood
Chamber holds two things the teardown purge (`hivemind.pheromone.retention.purge`) needs from it.
`end_night_veil` is its side channel: every live task still placed on the ending Cell is cancelled
first, since it can never finish anywhere now (its work was the Cell's, and a Night Veil Cell is
teardown-only, codingrules 8.7: a Hive stopping, an Absconding or a restart finding the Cell gone
all end one under a running task); then every finished Night Veil task, and every question it
asked, is reduced to ids, states and timestamps (`scrub_night_veil`: `hivemind.brood_chamber.
store.scrub`, through `TaskStore.scrub_night_veil`). `bound_to` is its member source: the live
tasks placed on the Cell and the Wardens they were assigned to, which a Queen that never held the
Cell's segment (a restart's sweep, an offline Absconding) has no other record of; the purge hands
them to every side channel, so the memory tables' rows about those tasks go too.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber.chamber`.
    Mixed into `BroodChamber`; called through the composition root's side channel
    (`hivemind.cli.compose.night_veil`). Calls into `hivemind.brood_chamber.store` (TaskFilter,
    MAX_TASK_FILTER_LIMIT), `hivemind.brood_chamber.task.state` and waggle only.

Key invariants:
    - `bound_to` reads only; `scrub_night_veil` rewrites words only, never a status or a time;
      `end_night_veil` changes no status but a cancellation of a task placed on the ending Cell.
    - `bound_to` never names the Cell itself, and reads live tasks alone: a finished task no
      longer names its Cell (its terminal edge clears the placement).
    - After `end_night_veil`, no live task names the Cell, and no finished Night Veil task among
      the ids it was handed keeps a word.

See Also:
    - hivemind.brood_chamber.store.scrub for what a reduced row keeps.
    - hivemind.pheromone.retention.purge for SideChannelPurger and MemberSource.
"""

from __future__ import annotations

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.store.protocol import MAX_TASK_FILTER_LIMIT, TaskFilter
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus, is_terminal
from waggle.ids import CellId

__all__: list[str] = []  # Private mixin: nothing here is part of the package's public API.

# Every status a placed task can hold: the only ones whose rows still name their Cell.
_LIVE = tuple(status for status in TaskStatus if not is_terminal(status))
# Why a task placed on an ending Night Veil Cell is cancelled (reduced away with its words).
_CELL_ENDED = "Its Night Veil Cell ended, and nothing of the Cell's work outlives it."


class _NightVeilMixin(_ChamberBase):
    """BroodChamber's Night Veil methods: end_night_veil, scrub_night_veil and bound_to."""

    async def end_night_veil(self, cell_id: CellId, task_ids: frozenset[str]) -> int:
        """Cancel every live task placed on `cell_id`, then reduce every finished Night Veil task.

        Args:
            cell_id: The Night Veil Cell ending now.
            task_ids: The ids its segment and member sources filed under it.

        Returns:
            How many task and question rows were reduced (a cancellation alone counts none).
        """
        cancelled = [
            await self._cancel(task, _CELL_ENDED) for task in await self._placed_on(cell_id)
        ]
        return await self.scrub_night_veil(task_ids | {task.id for task in cancelled})

    async def scrub_night_veil(self, task_ids: frozenset[str]) -> int:
        """Reduce every finished Night Veil task and its questions to their skeleton.

        Args:
            task_ids: The ids the Night Veil Cell's segment filed under it.

        Returns:
            How many task and question rows were rewritten.
        """
        return await self._store.scrub_night_veil(task_ids)

    async def bound_to(self, cell_id: CellId) -> frozenset[str]:
        """Return every live task placed on `cell_id`, and the Warden each was assigned to.

        Args:
            cell_id: The Night Veil Cell being purged.

        Returns:
            The task and Warden ids; empty when no live task names the Cell.
        """
        found: set[str] = set()
        for task in await self._placed_on(cell_id):
            found.add(task.id)
            if task.warden_id is not None:
                found.add(task.warden_id)
        return frozenset(found)

    async def _placed_on(self, cell_id: CellId) -> list[Task]:
        """Return every live task whose placement names `cell_id`."""
        return [
            task
            for status in _LIVE
            for task in await self._every(status)
            if task.cell_id == cell_id
        ]

    async def _every(self, status: TaskStatus) -> list[Task]:
        """Return every task in `status`, page by page: live tasks are few, but never capped."""
        tasks: list[Task] = []
        query = TaskFilter(status=status, limit=MAX_TASK_FILTER_LIMIT)
        while page := await self._store.list_tasks(query):
            tasks.extend(page)
            if len(page) < MAX_TASK_FILTER_LIMIT:
                break
            query = query.model_copy(update={"after": page[-1].id})
        return tasks
