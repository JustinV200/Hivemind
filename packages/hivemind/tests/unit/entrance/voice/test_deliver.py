"""Test hivemind.entrance.voice.deliver: a spoken goal, answer and chat go where typed ones go.

Over real listeners and a real Queen (her intake run by hand, or her loop running): a spoken goal
is echoed back and held AWAITING_CONFIRMATION, in the answer and in the chat, and nothing is
planned until a person confirms it; a declined one is refused and never planned; with
``confirm_goals`` off it is submitted as a typed goal is. A spoken answer resumes the task blocked
on its question with no confirmation, and reaches the asking Warden. Spoken chat enters the
Queen's inbox as a ``HumanMessage``. The typed goal's spend rules hold: an interactive device over
its cap steps up before anything is heard, and a program's goal is held for a person once heard.

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/deliver.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio

from builders.audio import silent_wav
from builders.entrance.serving import ProgramGrant, ServingRig, serving
from builders.entrance.voice import (
    PHONE,
    SPEAKER,
    SPEAKING,
    ask_the_human,
    drain,
    eventually,
    speak,
    voice_rig,
)
from builders.human import single_task_plan
from builders.queen import plan_responder

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.llm import FakeLLMProvider
from hivemind.llm.transcription import FakeTranscription
from hivemind.queen import ChatKind, ChatQuery, GoalRequestState
from hivemind.queen.chat import ECHO_PREFIX
from hivemind.queen.ticks.chat import human_items
from waggle.messages.control import HumanMessage

_WAV = silent_wav(1.0)
_GOAL = "Write a haiku about bees in the garden."  # What the transcriber hears for a goal.
_ANSWER = "Lavender, please."  # ...for an answer.
_CHAT = "How is the garden coming along?"  # ...for chat.


def _planner() -> FakeLLMProvider:
    """The Queen's model: every planning call answers a one-task plan."""
    return FakeLLMProvider(responder=plan_responder(single_task_plan))


async def _state(rig: ServingRig, request_id: str) -> GoalRequestState:
    """A goal request's state, as the Queen's table holds it."""
    return (await rig.deps.goal_requests.get(request_id)).state


async def test_a_spoken_goal_is_echoed_back_and_held_until_a_person_confirms_it() -> None:
    fake, planner = FakeTranscription(), _planner()
    fake.script(_GOAL)
    async with serving(voice_rig(fake, planner)) as rig:
        phone, session = await rig.program(PHONE)
        spoken = await speak(phone, session, _WAV, "goal")
        request_id = spoken.json()["goal"]["id"]
        await drain(rig)  # The Queen's intake leaves a held request to the human.
        held = await _state(rig, request_id)
        planned_before = list(planner.calls)
        echo = [line for line in await rig.deps.chat.read(ChatQuery()) if line.ref == request_id]
        confirmed = await phone.call(session, "POST", f"/v1/goals/{request_id}/confirm")
        await drain(rig)

        async def planned() -> bool:
            return await _state(rig, request_id) is GoalRequestState.PLANNED

        await eventually(planned)
        tasks = await rig.deps.chamber.list(TaskFilter(goal_request_id=request_id))

    assert spoken.status_code == 202, spoken.text
    body = spoken.json()
    assert (body["intent"], body["transcript"]) == ("goal", _GOAL)
    goal = body["goal"]
    assert (goal["state"], goal["source"], goal["needs_confirmation"]) == (
        "AWAITING_CONFIRMATION",
        "spoken",
        True,
    )
    assert held is GoalRequestState.AWAITING_CONFIRMATION and planned_before == []
    assert [(line.kind, line.text) for line in echo] == [(ChatKind.NOTICE, f"{ECHO_PREFIX}{_GOAL}")]
    assert confirmed.status_code == 200 and confirmed.json()["state"] == "RECEIVED"
    assert planner.calls and len(tasks) == 1


async def test_a_declined_spoken_goal_is_refused_and_never_planned() -> None:
    fake, planner = FakeTranscription(), _planner()
    fake.script(_GOAL)
    async with serving(voice_rig(fake, planner)) as rig:
        phone, session = await rig.program(PHONE)
        spoken = await speak(phone, session, _WAV, "goal")
        request_id = spoken.json()["goal"]["id"]
        path = f"/v1/goals/{request_id}/decline"
        declined = await phone.call(session, "POST", path, {"reason": "I said bees, not trees."})
        await drain(rig)
        state = await _state(rig, request_id)

    assert declined.status_code == 200 and declined.json()["refused"] is True
    assert state is GoalRequestState.REFUSED
    assert planner.calls == []


async def test_with_confirm_goals_off_a_spoken_goal_is_submitted_as_a_typed_one_is() -> None:
    fake, planner = FakeTranscription(), _planner()
    fake.script(_GOAL)
    async with serving(voice_rig(fake, planner, confirm_goals=False)) as rig:
        client, session = await rig.program(SPEAKER)
        spoken = await speak(client, session, _WAV, "goal")
        request_id = spoken.json()["goal"]["id"]
        echoes = [line for line in await rig.deps.chat.read(ChatQuery()) if line.ref == request_id]
        await drain(rig)

        async def planned() -> bool:
            return await _state(rig, request_id) is GoalRequestState.PLANNED

        await eventually(planned)

    goal = spoken.json()["goal"]
    assert (goal["state"], goal["source"], goal["needs_confirmation"]) == (
        "RECEIVED",
        "spoken",
        False,
    )
    assert echoes == []  # Nothing to confirm, so nothing echoed.
    assert planner.calls


async def test_a_spoken_answer_resumes_the_blocked_task_without_a_confirmation() -> None:
    fake = FakeTranscription()
    fake.script(_ANSWER)
    async with serving(voice_rig(fake, _planner())) as rig:
        client, session = await rig.program(SPEAKER)
        running = asyncio.ensure_future(rig.queen.run())
        try:
            question_id, task_id = await ask_the_human(rig)
            spoken = await speak(client, session, _WAV, f"answer:{question_id}")
            forwarded = await rig.warden_end.wait_for_answer()
            task = await rig.deps.chamber.get(task_id)
        finally:
            await rig.queen.stop()
            await running

    assert spoken.status_code == 202, spoken.text
    answered = spoken.json()["answered"]
    assert (answered["question_id"], answered["question_status"]) == (question_id, "ANSWERED")
    assert answered["task_status"] == "RUNNING" and task.status is TaskStatus.RUNNING
    assert forwarded.text == _ANSWER


async def test_spoken_chat_enters_the_queens_inbox_as_a_human_message() -> None:
    fake = FakeTranscription()
    fake.script(_CHAT)
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(SPEAKER)
        spoken = await speak(client, session, _WAV, "chat")
        [line] = await rig.deps.chat.read(ChatQuery())
        [item] = await human_items(rig.deps)

    assert spoken.json()["chat"]["id"] == line.id
    assert (line.kind, line.text, line.device_id) == (
        ChatKind.MESSAGE,
        _CHAT,
        session.key.device_id,
    )
    assert isinstance(item.payload, HumanMessage) and item.payload.text == _CHAT


async def test_an_interactive_device_over_its_cap_steps_up_before_anything_is_heard() -> None:
    fake = FakeTranscription()
    fake.script(_GOAL)
    capped = ProgramGrant(capabilities=SPEAKING, spend_cap_usd_per_day=1.0, interactive=True)
    async with serving(voice_rig(fake)) as rig:
        phone, session = await rig.program(capped)
        refused = await speak(phone, session, _WAV, "goal")
        heard_before = len(fake.calls)
        await phone.step_up(session)
        spoken = await speak(phone, session, _WAV, "goal")

    assert refused.status_code == 403
    assert (refused.json()["reason"], refused.json()["pending_id"]) == ("over_daily_cap", None)
    assert heard_before == 0
    assert spoken.status_code == 202, spoken.text


async def test_a_programs_spoken_goal_over_its_cap_is_held_for_a_person_once_heard() -> None:
    fake = FakeTranscription()
    fake.script(_GOAL)
    capped = ProgramGrant(capabilities=SPEAKING, spend_cap_usd_per_day=1.0)
    async with serving(voice_rig(fake)) as rig:
        client, session = await rig.program(capped)
        held = await speak(client, session, _WAV, "goal")
        console, console_session = await rig.console_session()
        await console.step_up(console_session)
        pending_id = held.json()["pending_id"]
        path = f"/v1/entrance/confirmations/{pending_id}/confirm"
        confirmed = await console.call(console_session, "POST", path)
        request_id = confirmed.json()["goal_request_id"]
        committed = await rig.deps.goal_requests.get(request_id)
        await drain(rig)
        echoed = await _state(rig, request_id)

    assert held.status_code == 403 and held.json()["reason"] == "over_daily_cap"
    assert pending_id is not None and len(fake.calls) == 1
    assert confirmed.status_code == 200, confirmed.text
    assert (committed.text, committed.source.value) == (_GOAL, "spoken")
    assert echoed is GoalRequestState.AWAITING_CONFIRMATION  # Still echoed once committed.
