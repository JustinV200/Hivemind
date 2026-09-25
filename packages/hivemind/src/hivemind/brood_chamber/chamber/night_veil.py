"""Define _NightVeilMixin: what BroodChamber gives the Night Veil teardown purge.

Codingrules section 12: a Night Veil Cell's work leaves nothing but its skeleton, and the Brood
Chamber holds two things the teardown purge (`hivemind.pheromone.retention.purge`) needs from it.
`scrub_night_veil` is its side channel: every finished Night Veil task, and every question it
asked, reduced to ids, states and timestamps (`hivemind.brood_chamber.store.scrub`, through
`TaskStore.scrub_night_veil`). `bound_to` is its member source: the live tasks placed on the
Cell and the Wardens they were assigned to, which a Queen that never held the Cell's segment (a
restart's sweep, an offline Absconding) has no other record of; the purge hands them to every
side channel, so the memory tables' rows about those tasks go too.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.brood_chamber.chamber`.
    Mixed into `BroodChamber`; called through the composition root's side channel
    (`hivemind.cli.compose.night_veil`). Calls into `hivemind.brood_chamber.store` (TaskFilter,
    MAX_TASK_FILTER_LIMIT), `hivemind.brood_chamber.task.state` and waggle only.

Key invariants:
    - `bound_to` reads only; `scrub_night_veil` rewrites words only, never a status or a time.
    - `bound_to` never names the Cell itself, and reads live tasks alone: a finished task no
      longer names its Cell (its terminal edge clears the placement).

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


class _NightVeilMixin(_ChamberBase):
    """BroodChamber's two Night Veil methods: scrub_night_veil and bound_to."""

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
        for status in _LIVE:
            for task in await self._every(status):
                if task.cell_id == cell_id:
                    found.add(task.id)
                    if task.warden_id is not None:
                        found.add(task.warden_id)
        return frozenset(found)

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
