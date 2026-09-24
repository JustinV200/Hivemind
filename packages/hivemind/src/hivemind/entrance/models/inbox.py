"""Define the inbox resource's bodies: what waits on the human, and the human's answers.

The human's inbox at the Landing Board (ADR-0032) is what the Queen could not settle herself: the
questions routed to the human (a task is blocked on each) and the Alarms that climbed the whole
chain. Reading it needs ``entrance:answer`` and ``honey:clearance:c2`` (a question may quote the
human's own data); answering goes to the Queen, who forwards it to the asking Warden and withdraws
the question from every other device.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.inbox``; published in the OpenAPI document. Calls into the Brood
    Chamber's question model, supervision's Alarm and pydantic.

Key invariants:
    - A question or Alarm is shown as the Hive holds it; nothing is summarised.

See Also:
    - hivemind.queen.human_inbox for what the Queen keeps waiting on the human.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.brood_chamber import Question, QuestionStatus, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.supervision import Alarm
from waggle.messages.base import TaskIdField

MAX_ANSWER_CHARS = 4_000  # The Brood Chamber's own bound on an answer: a few paragraphs.

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = [
    "AcknowledgedView",
    "AlarmView",
    "AnswerBody",
    "AnsweredView",
    "InboxView",
    "QuestionView",
    "alarm_view",
    "question_view",
]


class QuestionView(BaseModel):
    """A question waiting on the human."""

    model_config = _CONFIG

    id: str = Field(description="The question's id: answer it at .../questions/{id}/answer.")
    task_id: TaskIdField = Field(description="The task blocked on it.")
    text: str = Field(description="The question.")
    options: list[str] = Field(description="Closed choices, if any; empty means free text.")
    clearance: HoneyClearance = Field(description="The question text's sensitivity label.")
    asked_at: datetime = Field(description="When it was asked.")


class AlarmView(BaseModel):
    """An Alarm that reached the human."""

    model_config = _CONFIG

    id: str = Field(description="The Alarm's id: acknowledge it at .../alarms/{id}/acknowledge.")
    kind: str = Field(description="What went wrong, e.g. CELL_UNREACHABLE.")
    severity: str = Field(description="INFO, WARNING or CRITICAL.")
    detail: str = Field(description="The failing assertion, error sentence or observation.")
    task_id: TaskIdField | None = Field(description="The task concerned, if any.")
    raised_at: datetime = Field(description="When it was first raised.")


class InboxView(BaseModel):
    """Everything waiting on the human (C2)."""

    model_config = _CONFIG

    questions: list[QuestionView] = Field(description="Questions waiting, oldest first.")
    alarms: list[AlarmView] = Field(description="Alarms waiting, oldest first.")


class AnswerBody(BaseModel):
    """The human's answer to a question (``entrance:answer``)."""

    model_config = _CONFIG

    text: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS, description="The answer.")
    chosen_option: int | None = Field(
        default=None, ge=0, description="The index of the option picked, for a closed question."
    )


class AnsweredView(BaseModel):
    """A question answered: its task runs again."""

    model_config = _CONFIG

    question_id: str = Field(description="The question answered.")
    task_id: TaskIdField = Field(description="The task it unblocked.")
    task_status: TaskStatus = Field(description="The task's status now (RUNNING again).")
    question_status: QuestionStatus = Field(description="ANSWERED.")


class AcknowledgedView(BaseModel):
    """An Alarm acknowledged."""

    model_config = _CONFIG

    acknowledged: bool = Field(
        description="True when it was waiting and is now resolved; false when nothing was."
    )


def question_view(question: Question) -> QuestionView:
    """Shape a waiting question for the Landing Board.

    Args:
        question: The question as the Brood Chamber holds it.

    Returns:
        Its view.
    """
    return QuestionView(
        id=question.id,
        task_id=question.task_id,
        text=question.text,
        options=list(question.options),
        clearance=question.clearance,
        asked_at=question.asked_at,
    )


def alarm_view(alarm: Alarm) -> AlarmView:
    """Shape an Alarm waiting on the human for the Landing Board.

    Args:
        alarm: The Alarm as the Queen holds it.

    Returns:
        Its view.
    """
    return AlarmView(
        id=alarm.id,
        kind=alarm.kind.value,
        severity=alarm.severity.value,
        detail=alarm.detail,
        task_id=alarm.context.task_id,
        raised_at=alarm.raised_at,
    )
