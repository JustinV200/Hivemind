"""Tests for hivemind.queen.queen.Queen's human end: the chat, her REPLY, what reaches the human.

Roadmap step 10.5 (ADR-0040, "The chat is the human end of the Queen's inbox"): a message the
human posts wakes her through her own wake signal, with no Warden traffic at all; autopilot has no
rule for free text, so an awake episode decides, and a REPLY's words come back as a chat line.
Her questions and the Alarms that reached the human are appended to the same log, and every one
of these moments is handed to the `HumanChannel` seam the Hive Entrance implements. The episode's
prompt is read back from the `FakeLLMProvider`'s own record of what it was shown, so "the message
reached the model, fenced and labelled" is asserted on the exact words a model read.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_questions.py, test_queen_alarms.py and the rest. Exercises hivemind.queen.chat and
    hivemind.queen.ticks.human.chat/.awake together with the Queen's own tick.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.chat for the log and the seam under test.
    - tests/unit/queen/ticks/test_intake.py for goal requests, crash by crash.
"""

from __future__ import annotations

import asyncio

from builders.human import RecordingHumanChannel, is_planning, prompt_text, queen_responder
from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import AnswerSource, QuestionStatus, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.llm.models import LLMRequest
from hivemind.pheromone import TrailQuery
from hivemind.queen.chat import ChatAuthor, ChatEntry, ChatKind, ChatQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from hivemind.queen.ticks.awake import HUMAN_SECTION
from hivemind.supervision.capping.checks.human import LEAVE_QUESTION_OPTIONS
from waggle.clock import Clock
from waggle.ids import TaskId, WardenId, new_alarm_id, new_device_id, new_message_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised, Question

_WORDS = "Is the haiku about my niece Alice done yet?"  # C2 words: never on the trail.
_REPLY_WORDS = "Almost: the Drone is on the last line."
_REPLY = {"action": "REPLY", "reason": "The human asked for progress.", "message": _REPLY_WORDS}
_KEEP_FOR_GOAL = 1  # LEAVE_QUESTION_OPTIONS[1], "keep for this whole goal".
_C2 = HoneyClearance.C2  # A human's answer is always C2 (the Brood Chamber's Answer requires it).


async def _wait_for(deps: QueenDeps, kind: ChatKind, turns: int = 400) -> ChatEntry:
    """Yield the event loop until a chat line of `kind` exists, and return the first one."""
    for _ in range(turns):
        lines = [line for line in await deps.chat.read(ChatQuery()) if line.kind is kind]
        if lines:
            return lines[0]
        await asyncio.sleep(0)
    raise AssertionError(f"No {kind.value} line ever reached the chat.")


async def _wait_until_blocked(deps: QueenDeps, task_id: TaskId, turns: int = 400) -> None:
    """Yield the event loop until `task_id` is BLOCKED on a question."""
    for _ in range(turns):
        if (await deps.chamber.get(task_id)).status is TaskStatus.BLOCKED:
            return
        await asyncio.sleep(0)
    raise AssertionError("The task never blocked on its question.")


def _question(clock: Clock, task_id: TaskId, warden_id: WardenId, *, leave: bool) -> Question:
    """Build a Warden-forwarded Question; a leave one carries the closed leave options."""
    return Question(
        question_id=new_message_id(clock),
        task_id=task_id,
        asked_by=warden_id,
        text="Keep checker.exe?" if leave else "Which environment should the haiku target?",
        options=LEAVE_QUESTION_OPTIONS if leave else ("staging", "prod"),
        clearance=WireHoneyClearance.C1,
        asked_at=clock.now(),
    )


def _grant_exceeded(clock: Clock, task_id: TaskId) -> AlarmRaised:
    """Build an Alarm the shipped policy escalates to the human: only she divides Forage."""
    return AlarmRaised(
        alarm_id=new_alarm_id(clock),
        kind=AlarmKind.GRANT_EXCEEDED,
        severity=AlarmSeverity.WARNING,
        origin=new_worker_id(clock),
        attempts=0,
        raised_at=clock.now(),
        context=AlarmContext(
            task_id=task_id, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail="The grant's spend cap was reached.",
        clearance=WireHoneyClearance.C1,
        reason="Only the Queen can widen a grant.",
    )


async def test_a_human_message_wakes_the_queen_alone_and_her_reply_reaches_the_chat() -> None:
    seen: list[LLMRequest] = []
    channel = RecordingHumanChannel()
    provider = FakeLLMProvider(responder=queen_responder(_REPLY, seen))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, human_channel=channel)
    queen = Queen(deps)
    await queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    # The Warden stays silent throughout: only the wake signal the message sets can start a tick.
    message_id = await queen.post_human_message(_WORDS, new_device_id(deps.clock))
    reply = await _wait_for(deps, ChatKind.REPLY)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert (reply.author, reply.text, reply.ref) == (ChatAuthor.QUEEN, _REPLY_WORDS, message_id)
    assert await deps.chat.unhandled(10) == ()  # Decided once, so never read again.
    assert channel.names() == ["replied"]
    [episode] = [request for request in seen if not is_planning(request)]
    shown = prompt_text(episode)
    assert f"<<<{HUMAN_SECTION}>>>\n{_WORDS}\n<<<end {HUMAN_SECTION}>>>" in shown
    assert "could be compromised" in shown  # The label that says the words are data.
    assert not warden_end.intervenes and not warden_end.answers
    await warden_end.close()


async def test_the_trail_records_that_a_message_arrived_and_was_answered_never_the_words() -> None:
    provider = FakeLLMProvider(responder=queen_responder(_REPLY, []))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    await queen.post_human_message(_WORDS, new_device_id(deps.clock))
    await _wait_for(deps, ChatKind.REPLY)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    events = await deps.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    assert "queen.human_message_received" in kinds and "queen.replied" in kinds
    rendered = " ".join(event.model_dump_json() for event in events)
    assert "Alice" not in rendered and "last line" not in rendered
    await warden_end.close()


async def test_a_message_that_no_model_could_decide_gets_a_notice_not_silence() -> None:
    channel = RecordingHumanChannel()
    provider = FakeLLMProvider()
    provider.set_outage(True)  # Every rung and binding refuses, so no episode can decide.
    deps, link, warden_end = make_queen_deps(fake_provider=provider, human_channel=channel)
    queen = Queen(deps)
    await queen.attach_warden(link)
    run_task = asyncio.ensure_future(queen.run())

    message_id = await queen.post_human_message(_WORDS, new_device_id(deps.clock))
    notice = await _wait_for(deps, ChatKind.NOTICE)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert notice.ref == message_id
    assert await deps.chat.unhandled(10) == ()  # Settled with the notice, never retried forever.
    assert channel.names() == ["replied"]
    await warden_end.close()


async def test_a_question_for_the_human_reaches_the_chat_and_its_answer_closes_it() -> None:
    channel = RecordingHumanChannel()
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, human_channel=channel)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_question(deps.clock, goal_id, link.warden_id, leave=False))
    await _wait_until_blocked(deps, goal_id)
    line = await _wait_for(deps, ChatKind.QUESTION)
    [pending] = await queen.human_inbox.pending_questions(deps.chamber)
    await queen.answer_question(
        pending.id, "staging", source=AnswerSource.HUMAN, clearance=_C2, chosen_option=0
    )
    await warden_end.wait_for_answer()
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert (line.author, line.ref) == (ChatAuthor.QUEEN, pending.id)
    assert "staging" in line.text and "prod" in line.text  # The options the human picks from.
    assert channel.names() == ["question_asked", "question_closed"]
    assert channel.calls[-1] == ("question_closed", (pending.id, QuestionStatus.ANSWERED))
    await warden_end.close()


async def test_an_escalated_alarm_reaches_the_chat_and_acknowledging_it_resolves_it_once() -> None:
    channel = RecordingHumanChannel()
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, human_channel=channel)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    alarm = _grant_exceeded(deps.clock, goal_id)
    await warden_end.send(alarm)
    line = await _wait_for(deps, ChatKind.ALARM)
    first = await queen.acknowledge_alarm(alarm.alarm_id)
    again = await queen.acknowledge_alarm(alarm.alarm_id)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert (line.author, line.ref) == (ChatAuthor.QUEEN, alarm.alarm_id)
    assert (first, again) == (True, False)  # Resolved once; a second acknowledgement is a no-op.
    assert not queen.human_inbox.alarms
    assert channel.names() == ["alarm_raised", "alarm_acknowledged"]
    await warden_end.close()


async def test_the_in_process_answer_path_remembers_keep_for_this_whole_goal() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Install a checker.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_question(deps.clock, goal_id, link.warden_id, leave=True))
    await _wait_until_blocked(deps, goal_id)
    [pending] = await queen.human_inbox.pending_questions(deps.chamber)
    # The Hive Entrance answers through exactly this call (ADR-0040), never `hive inbox answer`.
    await queen.answer_question(
        pending.id,
        LEAVE_QUESTION_OPTIONS[_KEEP_FOR_GOAL],
        source=AnswerSource.HUMAN,
        clearance=_C2,
        chosen_option=_KEEP_FOR_GOAL,
    )
    await warden_end.wait_for_answer()
    # A second leaving for the same goal and Cell is answered from memory, never asked again.
    second = _question(deps.clock, goal_id, link.warden_id, leave=True)
    await warden_end.send(second)
    await warden_end.pump_until(lambda: len(warden_end.answers) > 1)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert (goal_id, link.cell.id) in queen._leave_memory
    assert warden_end.answers[-1].question_id == second.question_id
    assert await queen.human_inbox.pending_questions(deps.chamber) == ()
    await warden_end.close()


async def test_a_queen_or_warden_choice_of_keep_for_goal_is_never_remembered() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Install a checker.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_question(deps.clock, goal_id, link.warden_id, leave=True))
    await _wait_until_blocked(deps, goal_id)
    [pending] = await queen.human_inbox.pending_questions(deps.chamber)
    await queen.answer_question(
        pending.id, "keep for this whole goal", chosen_option=_KEEP_FOR_GOAL
    )
    await warden_end.wait_for_answer()
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    # Roadmap step 5.0d: only the human's own choice counts; the default source is the Queen.
    assert queen._leave_memory == {}
    await warden_end.close()


def _one_task_plan(goal: str) -> dict[str, object]:
    """The smallest valid plan for `goal`: one task, one acceptance criterion."""
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": "done.txt", "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }
