"""Deliver what a clip said: a goal echoed back (or submitted), an answer, or a chat line.

Once a clip is heard (roadmap step 10.5f), its words go where the same words typed would go, through
the Queen's door (ADR-0040: every write the Hive Entrance makes goes to her). A spoken goal is a
goal request with source ``spoken``: weighed first against its device's day by the typed goal's own
rule (``hivemind.entrance.intake.goal_spend``), and refused pending a step-up before anything is
heard when the device is interactive (a person steps up and speaks again), or held as a pending
confirmation for a person when it is a program, exactly as a typed goal is; then, when it must be
confirmed (``[entrance.voice] confirm_goals``, or the scanner flagged its words), committed, echoed
in the chat and held AWAITING_CONFIRMATION before the device is answered, so nothing is spent
until a person confirms it, and otherwise committed for the Queen to plan. A spoken answer goes
straight through, as the human's (C2) answer, and resumes the task blocked on it. Spoken chat is a
message in the chat, entering her inbox as a ``HumanMessage`` exactly as typed chat does.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.voice``. Called by the
    voice door. Calls into the Queen's door, the Entrance's intake, the gate's step-up and the
    goal, inbox and chat views.

Key invariants:
    - Nothing here is reached before the words are heard, scanned and within their bounds.
    - A goal that must be confirmed is AWAITING_CONFIRMATION (or already moved on) when this
      returns; one that need not be is RECEIVED, the Queen's to plan.

See Also:
    - hivemind.entrance.voice.door for the path that leads here.
    - hivemind.entrance.routes.goals for the confirm and decline routes a held goal waits on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pydantic import JsonValue

from hivemind.brood_chamber import AnswerSource, QuestionStatus
from hivemind.cell import HoneyClearance
from hivemind.entrance.auth.step_up import ActionKind, GoalSpend, requires_step_up
from hivemind.entrance.gate.admit import Caller
from hivemind.entrance.gate.errors import StepUpRequiredError
from hivemind.entrance.gate.services import EntranceServices
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.intake import goal_request, goal_spend
from hivemind.entrance.models import AnsweredView, ChatAccepted, GoalView, goal_view
from hivemind.entrance.models.inbox import MAX_ANSWER_CHARS
from hivemind.entrance.voice.errors import NothingHeardError, TranscriptTooLongError
from hivemind.entrance.voice.intent import VoiceIntentKind
from hivemind.queen import GoalSource
from hivemind.queen.chat import MAX_CHAT_TEXT_CHARS
from hivemind.queen.intake import MAX_GOAL_TEXT_CHARS
from waggle.ids import MessageId

SPOKEN_GOAL_CLEARANCE = HoneyClearance.C1  # A typed goal's own default ceiling, for its tasks.
# The most words each intent's outcome holds: a goal request, a chat line, a Brood Chamber answer.
WORD_LIMITS: Mapping[VoiceIntentKind, int] = MappingProxyType(
    {
        VoiceIntentKind.GOAL: MAX_GOAL_TEXT_CHARS,
        VoiceIntentKind.ANSWER: MAX_ANSWER_CHARS,
        VoiceIntentKind.CHAT: MAX_CHAT_TEXT_CHARS,
    }
)

__all__ = [
    "SPOKEN_GOAL_CLEARANCE",
    "WORD_LIMITS",
    "Delivered",
    "SpokenGoal",
    "deliver_answer",
    "deliver_chat",
    "deliver_goal",
    "weigh_goal",
    "words_of",
]


@dataclass(frozen=True, slots=True)
class SpokenGoal:
    """A spoken goal on its way to the Queen.

    Attributes:
        request_id: The goal request's id, minted before its words were scanned.
        words: What was heard, trimmed.
        spend: Its weight against its device's day (``weigh_goal``).
        confirm: Whether it must be echoed back and confirmed before it is planned.
    """

    request_id: str
    words: str
    spend: GoalSpend
    confirm: bool


@dataclass(frozen=True, slots=True)
class Delivered:
    """What a clip's words became, and the id a kept clip is filed under.

    Attributes:
        ref: The goal request, the question answered, or the chat line.
        goal: A goal's request view.
        answered: An answer's outcome.
        chat: A chat line's id.
    """

    ref: str
    goal: GoalView | None = None
    answered: AnsweredView | None = None
    chat: ChatAccepted | None = None


def words_of(text: str, kind: VoiceIntentKind) -> str:
    """Trim a transcript to the words it carries, within what its intent's outcome holds.

    Args:
        text: The transcript's text.
        kind: What the words are for.

    Returns:
        The words, trimmed.

    Raises:
        NothingHeardError: The transcript holds no words.
        TranscriptTooLongError: It holds more than the intent's outcome takes.
    """
    words = text.strip()
    if not words:
        raise NothingHeardError()
    limit = WORD_LIMITS[kind]
    if len(words) > limit:
        raise TranscriptTooLongError(len(words), limit)
    return words


async def weigh_goal(services: EntranceServices, caller: Caller) -> GoalSpend:
    """Weigh a spoken goal against its device's day, as a typed goal without a budget is.

    Args:
        services: The Entrance's services.
        caller: The device speaking.

    Returns:
        The goal's weight and the device's spend today.

    Raises:
        StepUpRequiredError: An interactive device needs a step-up first: refused now, before
            anything is heard, so it steps up and speaks again (a program's goal is held for a
            person once heard, by ``deliver_goal``).
    """
    spend = await goal_spend(services, caller.device, None)
    step_up_spend = services.rules.step_up_spend
    reason = requires_step_up(caller.session, ActionKind.GOAL, spend, step_up_spend=step_up_spend)
    if reason is not None and caller.session.interactive:
        raise StepUpRequiredError(reason)
    return spend


async def deliver_goal(services: EntranceServices, caller: Caller, goal: SpokenGoal) -> Delivered:
    """Commit a spoken goal: echoed back and held, or submitted, after the typed step-up rules.

    Args:
        services: The Entrance's services.
        caller: The device that spoke it.
        goal: The goal, its minted id, its weight and whether it must be confirmed.

    Returns:
        Its request's view: AWAITING_CONFIRMATION when echoed back, RECEIVED when submitted.

    Raises:
        StepUpRequiredError: A program's goal over its cap, held for a person (its pending id).
    """
    held: dict[str, JsonValue] = {
        "id": goal.request_id,
        "text": goal.words,
        "budget_usd": None,
        "comb_shield": None,
        "clearance": SPOKEN_GOAL_CLEARANCE.value,
        "source": GoalSource.SPOKEN.value,
        "needs_confirmation": goal.confirm,
    }
    # A program past its cap is held for a person here, as a typed goal is; the person confirms
    # the words they read in the hold, and a held spoken goal is still echoed once committed.
    await require_step_up(services, caller, ActionKind.GOAL, goal.spend, held)
    request = goal_request(services, caller.device, held)
    if goal.confirm:
        # Latency: local transactions in the Queen's tables (the row, its echo, its hold).
        echoed = await services.queen.request_echoed_goal(request)
        return Delivered(ref=request.id, goal=goal_view(echoed))
    # Latency: one local transaction in the Queen's tables, then her wake signal.
    await services.queen.request_goal(request)
    return Delivered(ref=request.id, goal=goal_view(request))


async def deliver_answer(
    services: EntranceServices, question_id: MessageId, words: str
) -> Delivered:
    """Answer a waiting question with the spoken words, straight through, as the human.

    Args:
        services: The Entrance's services.
        question_id: The question waiting on the human.
        words: What was heard.

    Returns:
        The question answered and the task it resumed.
    """
    # Latency: local Brood Chamber writes, then one Waggle send to the asking Warden.
    task = await services.queen.answer_question(
        question_id, words, source=AnswerSource.HUMAN, clearance=HoneyClearance.C2
    )
    answered = AnsweredView(
        question_id=question_id,
        task_id=task.id,
        task_status=task.status,
        question_status=QuestionStatus.ANSWERED,
    )
    return Delivered(ref=question_id, answered=answered)


async def deliver_chat(services: EntranceServices, caller: Caller, words: str) -> Delivered:
    """Post the spoken words to the chat, into the Queen's inbox as typed chat goes.

    Args:
        services: The Entrance's services.
        caller: The device that spoke them.
        words: What was heard.

    Returns:
        The chat line's id.
    """
    # Latency: one local transaction (the line and its event), then her wake signal.
    entry_id = await services.queen.post_human_message(words, caller.device.id)
    return Delivered(ref=entry_id, chat=ChatAccepted(id=entry_id))
