"""Define _QuestionsMixin: BroodChamber's ask, answer and withdraw methods.

A Worker or a Warden that cannot proceed without more information asks a Question
(`hivemind.brood_chamber.questions.Question`) against its task, moving the task RUNNING -> BLOCKED
until an Answer or a withdrawal moves it back. `ask` is the one method here that does not go
through `_ChamberBase._transition`, because it writes a new Question alongside the task
(`TaskStore.insert_question`, not `update_task`); `answer` and `withdraw` share almost everything
(load the question, assert its own transition, load its task, move both), so both route through
the private `_resolve_question` helper.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `BroodChamber`
    (`hivemind.brood_chamber.chamber`); not imported anywhere else. Calls into
    `hivemind.brood_chamber.chamber.base`, `hivemind.brood_chamber.questions`,
    `hivemind.brood_chamber.task.state` and `hivemind.cell` (HoneyClearance) only.

Key invariants:
    - `ask`'s question text and `withdraw`'s reason never appear in the TaskEvent payload each
      writes, only the question's id and (for `ask`) who asked (codingrules section 12).
    - `answer`/`withdraw` always move the task back to RUNNING and clear `pending_question_id`,
      whichever of the two ways the Question was resolved.

See Also:
    - hivemind.brood_chamber.questions for Question, Answer, QuestionStatus and
      assert_question_transition, the state machine this module enforces but never redefines.
    - hivemind.brood_chamber.chamber.base for _ChamberBase, the shared write helpers this mixin
      uses.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import JsonValue

from hivemind.brood_chamber.chamber.base import _ChamberBase
from hivemind.brood_chamber.questions import (
    Answer,
    Question,
    QuestionStatus,
    assert_question_transition,
)
from hivemind.brood_chamber.task.model import Task
from hivemind.brood_chamber.task.state import TaskStatus, assert_transition
from hivemind.cell import HoneyClearance
from waggle.ids import MessageId, TaskId, WardenId, WorkerId, new_message_id

__all__: list[str] = []  # Private mixin: nothing here is part of the package's public API.


class _QuestionsMixin(_ChamberBase):
    """BroodChamber's Question methods: ask, answer and withdraw."""

    async def ask(
        self,
        task_id: TaskId,
        asked_by: WorkerId | WardenId,
        text: str,
        options: Sequence[str] = (),
        clearance: HoneyClearance = HoneyClearance.C1,
    ) -> Question:
        """Move a task RUNNING -> BLOCKED, raising a new Question against it.

        Args:
            task_id: The task that cannot proceed without an answer.
            asked_by: The Worker or Warden asking.
            text: The question itself. Never copied into the trail event.
            options: Closed choices to offer, if any.
            clearance: The question text's data-sensitivity label.

        Returns:
            The new Question, status ASKED.
        """
        task = await self._store.get_task(task_id)
        assert_transition(task.status, TaskStatus.BLOCKED, task_id=task.id)
        now = self._clock.now()
        question = Question(
            id=new_message_id(self._clock),
            task_id=task.id,
            asked_by=asked_by,
            text=text,
            options=tuple(options),
            clearance=clearance,
            asked_at=now,
        )
        new_task = task.model_copy(
            update={
                "status": TaskStatus.BLOCKED,
                "updated_at": now,
                "pending_question_id": question.id,
            }
        )
        payload: dict[str, JsonValue] = {"question_id": question.id, "asked_by": asked_by}
        event = self._build_event(task.id, "task.blocked", payload, now)
        await self._store.insert_question(new_task, question, event)
        return question

    async def answer(self, question_id: MessageId, answer: Answer) -> Task:
        """Answer a Question, moving its task back BLOCKED -> RUNNING.

        Args:
            question_id: The question being answered.
            answer: The answer to record.

        Returns:
            The question's task, now RUNNING with `pending_question_id` cleared.
        """
        payload: dict[str, JsonValue] = {"question_id": question_id, "source": answer.source.value}
        return await self._resolve_question(
            question_id, QuestionStatus.ANSWERED, "task.answered", payload, answer=answer
        )

    async def withdraw(self, question_id: MessageId, reason: str) -> Task:
        """Withdraw a Question without an answer, moving its task back BLOCKED -> RUNNING.

        Args:
            question_id: The question being withdrawn.
            reason: Why it no longer needs an answer, for the trail.

        Returns:
            The question's task, now RUNNING with `pending_question_id` cleared.
        """
        payload: dict[str, JsonValue] = {"question_id": question_id, "reason": reason}
        return await self._resolve_question(
            question_id, QuestionStatus.WITHDRAWN, "task.question_withdrawn", payload
        )

    async def _resolve_question(
        self,
        question_id: MessageId,
        new_status: QuestionStatus,
        kind: str,
        payload: Mapping[str, JsonValue],
        answer: Answer | None = None,
    ) -> Task:
        """Move a Question to a terminal status and its Task back to RUNNING, atomically."""
        question = await self._store.get_question(question_id)
        assert_question_transition(question.status, new_status, question_id=question.id)
        task = await self._store.get_task(question.task_id)
        now = self._clock.now()
        new_question = question.model_copy(update={"status": new_status, "answer": answer})
        new_task = task.model_copy(
            update={"status": TaskStatus.RUNNING, "updated_at": now, "pending_question_id": None}
        )
        event = self._build_event(task.id, kind, payload, now)
        await self._store.update_question(new_task, new_question, event)
        return new_task
