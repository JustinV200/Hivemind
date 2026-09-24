"""Serve the inbox resource: what waits on the human, and the human's answers and acknowledgements.

The human's inbox (ADR-0032) holds the questions the Queen routed to the human and the Alarms that
climbed the whole chain. ``GET /v1/inbox`` reads both (``C2``: a question may quote the human's own
data). Answering a question goes to the Queen with source ``HUMAN`` and clearance ``C2``: she
records it, forwards it to the asking Warden, resumes the task and withdraws the question from every
device that was told of it. Acknowledging an Alarm resolves it and withdraws it the same way. All
three need ``entrance:answer``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    the route table. Calls into the Queen's door and, for waiting questions, the Brood Chamber.

Key invariants:
    - An answer is always recorded as the human's, at C2 (the human's own words).

See Also:
    - hivemind.queen.questions for what answering does.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.brood_chamber import AnswerSource, QuestionStatus
from hivemind.cell import HoneyClearance
from hivemind.entrance.gate.params import Services
from hivemind.entrance.gate.spec import BOTH_LISTENERS, RouteEffect, RouteSpec, session_with
from hivemind.entrance.models import (
    AcknowledgedView,
    AnswerBody,
    AnsweredView,
    InboxView,
    alarm_view,
    question_view,
)
from waggle.messages.base import AlarmIdField, MessageIdField

ANSWER = "entrance:answer"  # What every inbox route needs.

QuestionIdPath = Annotated[MessageIdField, Path(description="The question's id.")]
AlarmIdPath = Annotated[AlarmIdField, Path(description="The Alarm's id.")]

__all__ = ["ROUTES"]


async def read_inbox(services: Services) -> InboxView:
    """Read every question and Alarm waiting on the human.

    Args:
        services: The Entrance's services.

    Returns:
        The questions and Alarms, oldest first.
    """
    queen = services.queen
    # Latency: one local read of the Brood Chamber's pending questions.
    questions = await queen.human_inbox.pending_questions(services.hive.chamber)
    alarms = sorted(queen.human_inbox.alarms, key=lambda alarm: (alarm.raised_at, alarm.id))
    return InboxView(
        questions=[question_view(question) for question in questions],
        alarms=[alarm_view(alarm) for alarm in alarms],
    )


async def answer(question_id: QuestionIdPath, body: AnswerBody, services: Services) -> AnsweredView:
    """Answer a question as the human; the Queen forwards it and resumes the task.

    Args:
        question_id: The question.
        body: The answer, and the option picked for a closed question.
        services: The Entrance's services.

    Returns:
        The task it unblocked and its status now.
    """
    task = await services.queen.answer_question(
        question_id,
        body.text,
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
        chosen_option=body.chosen_option,
    )
    return AnsweredView(
        question_id=question_id,
        task_id=task.id,
        task_status=task.status,
        question_status=QuestionStatus.ANSWERED,
    )


async def acknowledge(alarm_id: AlarmIdPath, services: Services) -> AcknowledgedView:
    """Acknowledge an Alarm that reached the human; it is resolved and withdrawn everywhere.

    Args:
        alarm_id: The Alarm.
        services: The Entrance's services.

    Returns:
        Whether it was waiting and is now resolved.
    """
    return AcknowledgedView(acknowledged=await services.queen.acknowledge_alarm(alarm_id))


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/inbox",
        listeners=BOTH_LISTENERS,
        access=session_with(ANSWER, c2=True),
        effect=RouteEffect.READ,
        endpoint=read_inbox,
        summary="Read the questions and Alarms waiting on the human (C2).",
        response_model=InboxView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/inbox/questions/{question_id}/answer",
        listeners=BOTH_LISTENERS,
        access=session_with(ANSWER),
        effect=RouteEffect.INBOX,
        endpoint=answer,
        summary="Answer a question as the human.",
        response_model=AnsweredView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/inbox/alarms/{alarm_id}/acknowledge",
        listeners=BOTH_LISTENERS,
        access=session_with(ANSWER),
        effect=RouteEffect.INBOX,
        endpoint=acknowledge,
        summary="Acknowledge an Alarm that reached the human.",
        response_model=AcknowledgedView,
    ),
)
