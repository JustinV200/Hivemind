"""Define ChamberIdentity and the shared plumbing every BroodChamber mixin builds on.

`ChamberIdentity` is which Hive, which node (a Queen or Warden process) and which actor (a bee id,
or the literal "human"/"system") a `BroodChamber` (`hivemind.brood_chamber.chamber`) stamps on
every `TaskEvent` (`hivemind.pheromone`, the Pheromone Trail's audit record of who did what to
what and when) it writes. `_ChamberBase` is the private base every mixin in this package
(`submission.py`, `lifecycle.py`, `outcomes.py`, `questions.py`, `queries.py`) inherits: it holds
the `TaskStore`, `Clock` and `ChamberIdentity` every method needs, and the private helpers
(`_build_event`, `_write`, `_transition`) that turn "apply these field updates and write a matching
event" into one call, so no mixin repeats the `TaskEvent(...)` construction by hand; `_cancel` is
the one cancellation both `outcomes.py` and `night_veil.py` write.

`_build_event` is also where a Night Veil task's events are cut to the skeleton (codingrules
section 12: "task state transitions carrying nothing beyond the task id"). A task is Night Veil
when a human asked for it at that tier (`spec.needs.comb_shield`) or it is bound to a Night Veil
Cell (`bound_tier`), and every event this chamber writes for one carries an empty payload: the
title a planner writes from the human's goal (C2), the Cell and Warden it lands on, a question's
id, a cancel's reason. The cut happens here, at the source, because each event is written with
its task row in one store transaction (codingrules Appendix C rule 3), never through the Queen's
trail decorator (`hivemind.pheromone.retention.VeiledTrail`); what the chamber's own rows hold
is unchanged, so nothing that reads a task loses anything.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Inherited by every other module in this
    package; nothing outside `hivemind.brood_chamber.chamber` imports it directly (the package's
    `__init__.py` is the public face).
    Calls into `hivemind.brood_chamber.task.state` (assert_transition), `hivemind.cell`
    (CombShieldLevel) and `hivemind.pheromone` (TaskEvent, skeleton_event) only.

Key invariants:
    - `_transition` never writes a `Task` whose `status` a caller has not already had
      `hivemind.brood_chamber.task.state.assert_transition` approve.
    - Every `TaskEvent` `_build_event` builds is stamped with this chamber's own `identity` and a
      freshly minted id; no caller builds a `TaskEvent` by hand.
    - A Night Veil task's event, before or after the write it records, carries no payload.

See Also:
    - hivemind.brood_chamber.chamber for BroodChamber, the class every mixin here composes into.
    - hivemind.brood_chamber.task.state for the transition table `_transition` enforces.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from hivemind.brood_chamber.store.protocol import TaskStore
from hivemind.brood_chamber.task.model import Task, TaskOutcome
from hivemind.brood_chamber.task.state import TaskStatus, assert_transition
from hivemind.cell import CombShieldLevel
from hivemind.pheromone import TaskEvent, skeleton_event
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
        self,
        task: Task,
        kind: str,
        payload: Mapping[str, JsonValue],
        at: datetime,
        after: Task | None = None,
    ) -> TaskEvent:
        """Build one TaskEvent about `task`, stamped `at`, cut to the skeleton for Night Veil.

        Args:
            task: The task the event is about, as it stood before the write.
            kind: One of `TaskEvent.KINDS`.
            payload: Ids, enums and counts only (codingrules section 12).
            at: When the write happened.
            after: The task as the write leaves it, when the write changes it; a task that becomes
                (or stops being) bound to a Night Veil Cell is Night Veil on either side.
        """
        event = TaskEvent(
            id=new_event_id(self._clock),
            hive_id=self._identity.hive_id,
            node_id=self._identity.node_id,
            at=at,
            actor=self._identity.actor,
            kind=kind,
            subject_id=task.id,
            payload=dict(payload),
        )
        if not (_is_night_veil(task) or (after is not None and _is_night_veil(after))):
            return event
        # The skeleton keeps every task kind with nothing beyond the task id (module docstring);
        # should it ever stop naming one, the transition is still recorded, just as bare.
        cut = skeleton_event(event)
        return cut if cut is not None else event.model_copy(update={"payload": {}})

    async def _write(
        self, task: Task, kind: str, payload: Mapping[str, JsonValue], **updates: object
    ) -> Task:
        """Apply `updates` to `task`, write it and a matching TaskEvent together, then return it."""
        now = self._clock.now()
        new_task = task.model_copy(update={"updated_at": now, **updates})
        event = self._build_event(task, kind, payload, now, after=new_task)
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

    async def _cancel(self, task: Task, reason: str) -> Task:
        """Move non-terminal `task` -> CANCELLED, its outcome built from `reason`; see `cancel`."""
        outcome = TaskOutcome(status=TaskStatus.CANCELLED, summary=reason)
        payload: dict[str, JsonValue] = {"reason": reason, "from_status": task.status.value}
        # A cancelled task is never placed, nor still blocked on a question (BLOCKED -> CANCELLED).
        return await self._transition(
            task,
            TaskStatus.CANCELLED,
            "task.cancelled",
            payload,
            outcome=outcome,
            warden_id=None,
            cell_id=None,
            bound_tier=None,
            pending_question_id=None,
        )


def _is_night_veil(task: Task) -> bool:
    """Return whether `task` runs at Night Veil: asked for at that tier, or bound to such a Cell."""
    return CombShieldLevel.NIGHT_VEIL in (task.spec.needs.comb_shield, task.bound_tier)
