"""Tests for hivemind.wardens.ticks.control and .questions: relaying between Queen and sub-bee.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/control.py and questions.py (codingrules section 3); split
    by feature (14.2) from test_warden_lifecycle.py, test_warden_spawn_and_accept.py,
    test_warden_alarms.py and test_warden_heartbeat.py. Drives the two tick handlers directly
    against a real Warden and a real sub-bee link, without a running WorkerRuntime, so the
    forwarding mechanics are tested deterministically (roadmap step 3.19's own dispatch map).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.control, hivemind.wardens.ticks.questions for the modules under test.
"""

from __future__ import annotations

import asyncio

from builders.wardens import make_warden_deps
from builders.workers import make_assignment

from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.control import forward_control
from hivemind.wardens.ticks.questions import forward_answer, forward_question
from hivemind.wardens.warden import Warden
from hivemind.workers.state import WorkerState
from waggle.codec import Codec
from waggle.envelope import wrap
from waggle.ids import new_message_id, new_task_id, new_worker_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Answer, AnswerSource, Question
from waggle.messages.task import TaskCancel, TaskPause, TaskResume
from waggle.transport.memory import MemoryTransport


async def _make_sub_bee(warden: Warden) -> tuple[SubBee, MemoryTransport]:
    """Build a SubBee over a real MemoryTransport pair, with no runtime behind it."""
    assignment = make_assignment(clock=warden._deps.clock)
    warden_link, other_end = MemoryTransport.pair(Codec(), Codec())
    dummy_task: asyncio.Task[None] = asyncio.ensure_future(_noop())
    await dummy_task
    sub_bee = SubBee(
        worker_id=new_worker_id(warden._deps.clock),
        task_id=assignment.task_id,
        assignment=assignment,
        attempt=1,
        state=WorkerState.RUNNING,
        binding="worker",
        last_handoff=None,
        link=warden_link,
        runtime_task=dummy_task,
    )
    warden._sub_bees[sub_bee.worker_id] = sub_bee
    return sub_bee, other_end


async def _noop() -> None:
    return None


async def test_forward_control_relays_task_cancel_to_the_sub_bees_own_link() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    sub_bee, other_end = await _make_sub_bee(warden)
    cancel = TaskCancel(task_id=sub_bee.task_id, grace_s=0.0, reason="stop")

    await forward_control(warden, sub_bee, cancel)

    envelope = await anext(other_end.receive())
    assert envelope.payload == cancel
    assert envelope.recipient == sub_bee.worker_id
    assert envelope.sender == warden_id


async def test_forward_control_relays_pause_and_resume() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    sub_bee, other_end = await _make_sub_bee(warden)

    await forward_control(warden, sub_bee, TaskPause(task_id=sub_bee.task_id, reason="pause"))
    await forward_control(
        warden,
        sub_bee,
        TaskResume(task_id=sub_bee.task_id, attempt=1, resume_from=None, slot=None, reason="go"),
    )

    first = await anext(other_end.receive())
    second = await anext(other_end.receive())
    assert isinstance(first.payload, TaskPause)
    assert isinstance(second.payload, TaskResume)


async def test_forward_control_is_a_no_op_when_no_sub_bee_is_named() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    cancel = TaskCancel(task_id=new_task_id(deps.clock), grace_s=0.0, reason="stop")

    await forward_control(warden, None, cancel)  # must not raise


async def test_forward_question_then_forward_answer_round_trip_by_question_id() -> None:
    deps, queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    sub_bee, other_end = await _make_sub_bee(warden)
    question = Question(
        question_id=new_message_id(deps.clock),
        task_id=sub_bee.task_id,
        asked_by=sub_bee.worker_id,
        text="what next?",
        options=(),
        clearance=WireHoneyClearance.C1,
        asked_at=deps.clock.now(),
    )
    # Simulate the sub-bee's own Question arriving on the Warden's queen link: forward_question
    # only needs the envelope id it arrived on, not a full pumped receive here.
    envelope = wrap(question, deps.hop, clock=deps.clock)

    await forward_question(warden, envelope.id, question)

    forwarded = await queen_end.wait_for_question()
    assert forwarded.question_id == question.question_id
    assert warden._questions[question.question_id] == sub_bee.worker_id
    assert warden._question_envelope_ids[question.question_id] == envelope.id

    answer = Answer(
        question_id=question.question_id,
        task_id=sub_bee.task_id,
        text="go ahead",
        chosen_option=None,
        source=AnswerSource.QUEEN,
        clearance=WireHoneyClearance.C1,
    )
    await forward_answer(warden, answer)

    delivered = await anext(other_end.receive())
    assert delivered.payload == answer
    assert delivered.recipient == sub_bee.worker_id


async def test_forward_answer_is_a_no_op_for_an_unknown_question_id() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    stray = Answer(
        question_id=new_message_id(deps.clock),
        task_id=new_task_id(deps.clock),
        text="too late",
        chosen_option=None,
        source=AnswerSource.QUEEN,
        clearance=WireHoneyClearance.C1,
    )

    await forward_answer(warden, stray)  # must not raise
