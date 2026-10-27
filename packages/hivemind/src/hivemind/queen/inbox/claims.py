"""Refuse a Warden's message about a task not placed on its Cell, before anything applies it.

A Warden speaks for the tasks placed on its own Cell: their results, their progress, its bees'
Alarms, questions and answers. The Queen's autopilot table turns each of those into an action on
the task it names (complete it, retry or fail it, block it on a question, pause it), so a Warden
naming a task placed on another Cell, or on none, could finish, fail or block work that is not its
own. Every Warden link proves its Warden at its handshake, which is the principal of every inbox
item drained off it, and the Brood Chamber says which Warden each task is placed with.
`TaskClaims.refuse` compares the two for every item a Warden sent that names a live task; a
mismatch is refused, never decided or applied, and recorded as `guard.task_refused` about the
sending Cell (reason `another_cell` when the task is placed with another Warden, `unplaced` when
with none), once per Warden, message kind and reason, which the Guard Bee's judged `task_claim`
rule counts. A report about a task that has already ended stays the quiet no-op the autopilot
table makes it: nothing can be applied to a finished task.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's inbox
    sub-package. Built once by `hivemind.queen.queen.Queen`, and called by her tick for every inbox
    item after the task it names is read and before `decide`. Calls into `hivemind.brood_chamber`
    (Task, is_terminal), `hivemind.common.logging`, `hivemind.pheromone` (GuardEvent),
    `hivemind.supervision.attendant` (InboxItem) and waggle only; `QueenDeps` and `WardenLink`
    only for their types.

Key invariants:
    - No item a Warden sent about a live task placed elsewhere, or nowhere, reaches `decide`.
    - A refusal names the sending Cell and Warden and the message's kind, never the task, so it
      never files the sender's record under another Cell (a Night Veil Cell's, say).
    - The human's own messages, and every item that names no task, pass untouched.

See Also:
    - hivemind.queen.autopilot.table for what a Warden's message would otherwise do to a task.
    - hivemind.queen.attach.refusals for a link's own refusals, the same record's siblings.
    - hivemind/workers/roles/guard_bee/rules.toml for `task_claim`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task, is_terminal
from hivemind.common.logging import get_logger
from hivemind.pheromone import GuardEvent
from hivemind.supervision.attendant import InboxItem
from waggle.errors import InvalidIdError
from waggle.ids import IdKind, WardenId, new_event_id, parse_id

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from collections.abc import Mapping

    from hivemind.queen.deps import QueenDeps, WardenLink

log = get_logger(__name__)

TASK_REFUSED_KIND = "guard.task_refused"

__all__ = ["TASK_REFUSED_KIND", "TaskClaims", "TaskRefusal"]


class TaskRefusal(StrEnum):
    """Why a Warden's message about a task was refused."""

    ANOTHER_CELL = "another_cell"  # The task is placed with another Warden, on another Cell.
    UNPLACED = "unplaced"  # The task is placed with no Warden at all right now.


class TaskClaims:
    """The Queen's check that a Warden only ever speaks for tasks placed on its own Cell."""

    def __init__(self) -> None:
        """Start having recorded nothing."""
        # Codingrules 8.5: grows only in `_record`, one entry per Warden, message kind and reason.
        self._recorded: set[tuple[str, str, str]] = set()

    async def refuse(
        self,
        deps: QueenDeps,
        wardens: Mapping[WardenId, WardenLink],
        item: InboxItem,
        task: Task | None,
    ) -> bool:
        """Refuse `item` when its Warden names a live task not placed with it; record why.

        Args:
            deps: The Queen's collaborators; `trail` records the refusal.
            wardens: Every attached Warden, for the sending Warden's Cell.
            item: One ordered inbox item; its principal is the Warden whose link carried it.
            task: The task `item` names, as the Brood Chamber has it; None when it names none.

        Returns:
            True when `item` is refused and must not be decided; False when it may be.
        """
        if task is None or is_terminal(task.status) or not _from_a_warden(item):
            return False  # No task, a finished one (a no-op), or the human's own words.
        if task.warden_id == item.principal:
            return False  # Its own task: the one thing a Warden may speak for.
        reason = TaskRefusal.UNPLACED if task.warden_id is None else TaskRefusal.ANOTHER_CELL
        await self._record(deps, wardens.get(WardenId(item.principal)), item, reason)
        return True

    async def _record(
        self, deps: QueenDeps, link: WardenLink | None, item: InboxItem, reason: TaskRefusal
    ) -> None:
        """Record one refusal about the sending Cell, unless this one was recorded already."""
        key = (item.principal, item.payload_kind, reason.value)
        if key in self._recorded:
            return
        self._recorded.add(key)
        # A Warden that detached since its item was drained names no Cell: the Warden stands in.
        cell_id = str(link.cell.id) if link is not None else None
        event = GuardEvent(
            id=new_event_id(deps.clock),
            hive_id=deps.identity.hive_id,
            node_id=deps.identity.node_id,
            at=deps.clock.now(),
            actor="system",
            kind=TASK_REFUSED_KIND,
            subject_id=cell_id if cell_id is not None else item.principal,
            payload={
                "cell_id": cell_id,
                "warden_id": item.principal,
                "message": item.payload_kind,
                "reason": reason.value,
            },
        )
        await deps.trail.record(event)
        log.warning("queen.task_refused", warden_id=item.principal, reason=reason.value)


def _from_a_warden(item: InboxItem) -> bool:
    """Return whether `item` came off a Warden's link: its principal is a Warden's id."""
    try:
        parse_id(item.principal, IdKind.WARDEN)
    except InvalidIdError:
        return False  # The human, or the Guard Bee filing through the Queen's door.
    return True
