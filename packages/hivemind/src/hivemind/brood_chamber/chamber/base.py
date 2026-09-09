"""Define ChamberIdentity and the shared plumbing every BroodChamber mixin builds on.

`ChamberIdentity` is which Hive, which node (a Queen or Warden process) and which actor (a bee id,
or the literal "human"/"system") a `BroodChamber` (`hivemind.brood_chamber.chamber`) stamps on
every `TaskEvent` (`hivemind.pheromone`, the Pheromone Trail's audit record of who did what to
what and when) it writes. `_ChamberBase` is the private base every mixin in this package
(`submission.py`, `lifecycle.py`, `outcomes.py`, `questions.py`, `queries.py`) inherits: it holds
the `TaskStore`, `Clock` and `ChamberIdentity` every method needs, and the three private helpers
(`_build_event`, `_write`, `_transition`) that turn "apply these field updates and write a matching
event" into one call, so no mixin repeats the `TaskEvent(...)` construction by hand.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Inherited by every other module in this
    package; nothing outside `hivemind.brood_chamber.chamber` imports it directly (the package's
    `__init__.py` is the public face).
    Calls into `hivemind.brood_chamber.task_state` (assert_transition) and `hivemind.pheromone`
    (TaskEvent) only.

Key invariants:
    - `_transition` never writes a `Task` whose `status` a caller has not already had
      `hivemind.brood_chamber.task_state.assert_transition` approve.
    - Every `TaskEvent` `_build_event` builds is stamped with this chamber's own `identity` and a
      freshly minted id; no caller builds a `TaskEvent` by hand.

See Also:
    - hivemind.brood_chamber.chamber for BroodChamber, the class every mixin here composes into.
    - hivemind.brood_chamber.task_state for the transition table `_transition` enforces.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from hivemind.brood_chamber.store import TaskStore
from hivemind.brood_chamber.task import Task
from hivemind.brood_chamber.task_state import TaskStatus, assert_transition
from hivemind.pheromone import TaskEvent
from waggle.clock import Clock
from waggle.ids import HiveId, NodeId, new_event_id

__all__ = ["ChamberIdentity"]


@dataclass(frozen=True, slots=True)
class ChamberIdentity:
    """The Hive, node and actor a BroodChamber stamps on every TaskEvent it writes.

    No Hive Manifest exists yet to supply this (phase 3), so it is injected at construction
    instead (docs/PHASE2_BRIEF.md decision 3). Reads (`get`, `list`, `pending_questions`) need no
    real identity; a caller that only reads may pass any well-formed placeholder.

    Attributes:
        hive_id: The Hive this chamber's events belong to.
        node_id: This process's own node id (a Queen or a Warden), carried on every event so a
            merged trail can tell which node recorded it.
        actor: Who this chamber acts as: a bee id, or the literal "human" or "system"
            (`hivemind.pheromone.events.base.ACTOR_LITERALS`).
    """

    hive_id: HiveId
    node_id: NodeId
    actor: str


class _ChamberBase:
    """The store, clock and identity every BroodChamber mixin shares, plus their write helpers.

    Every mixin in this package subclasses this one, so `self._store`, `self._clock` and
    `self._identity` (and the three helpers below) are available without repeating them.
    """

    # Declared, not assigned: every mixin subclasses this alongside the others, and only
    # BroodChamber's own __init__ (inherited from here) actually sets these. The bare annotations
    # are what let mypy --strict see the attributes' types inside every mixin method.
    _store: TaskStore
    _clock: Clock
    _identity: ChamberIdentity

    def __init__(self, store: TaskStore, clock: Clock, identity: ChamberIdentity) -> None:
        """Wrap a TaskStore with the clock and identity every write needs.

        Args:
            store: Where every Task and Question this chamber touches is persisted.
            clock: Injected time source for every id minted and every timestamp written.
            identity: The Hive, node and actor this chamber stamps on every TaskEvent.
        """
        self._store = store
        self._clock = clock
        self._identity = identity

    def _build_event(
        self, subject_id: str, kind: str, payload: Mapping[str, JsonValue], at: datetime
    ) -> TaskEvent:
        """Build one TaskEvent stamped `at`, from this chamber's own identity."""
        return TaskEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=at,
            actor=self._identity.actor,
            kind=kind,
            subject_id=subject_id,
            payload=dict(payload),
        )

    async def _write(
        self, task: Task, kind: str, payload: Mapping[str, JsonValue], **updates: object
    ) -> Task:
        """Apply `updates` to `task`, write it and a matching TaskEvent together, then return it."""
        now = self._clock.now()
        new_task = task.model_copy(update={"updated_at": now, **updates})
        event = self._build_event(task.id, kind, payload, now)
        await self._store.update_task(new_task, event)
        return new_task

    async def _transition(
        self,
        task: Task,
        new_status: TaskStatus,
        kind: str,
        payload: Mapping[str, JsonValue],
        **updates: object,
    ) -> Task:
        """Move `task` to `new_status` (asserted legal first) and write it plus its TaskEvent."""
        # The one gate every status change passes through: task_state.TRANSITIONS decides
        # legality, never a caller's own judgement.
        assert_transition(task.status, new_status, task_id=task.id)
        return await self._write(task, kind, payload, status=new_status, **updates)
