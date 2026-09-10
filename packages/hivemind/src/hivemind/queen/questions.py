"""Define handle_question, answer_question and route_answers: the Queen's own question traffic.

Roadmap step 3.20's own dispatch map: "Question from a sub-bee -> BLOCK_ON_QUESTION: chamber.ask
(task BLOCKED, pending_question_id) -> visible in the human inbox; Answer arrives via chamber.answer
(the CLI's `hive inbox answer`): questions.route_answers finds answered questions the Queen has not
yet forwarded and sends an Answer envelope to the Warden that asked."

`handle_question` is the first half: `chamber.ask` with the *original* asker (`Question.asked_by`
survives forwarding on the wire, so the Warden's own name never overwrites who actually asked).
It returns the chamber's own newly-minted `Question`, which never reuses the wire message's own
`question_id` (`hivemind.brood_chamber.questions`'s own module docstring: the chamber's `Question`
is "a plain stored value with its own status machine", not the wire form, so `chamber.ask` always
mints a fresh id `pending_questions` can read back after a restart) -- so a caller must remember
both ids, plus the arriving envelope's own id (see below), to answer correctly (`queen.
answer_question`'s own `_question_wire_ids`/`_question_envelope_ids`).
`answer_question` is the real forwarding path: `hivemind.brood_chamber.BroodChamber`'s own public
API exposes no way to read an already-`ANSWERED` `Question`'s content back out (no `get_question`,
and `pending_questions` filters to `ASKED` only -- `brood_chamber/**` is outside this dispatch's
owned files, so no method could be added there to close that gap). `answer_question` is therefore
the one path a caller (`hive inbox answer`, roadmap step 3.21) should use instead of calling
`chamber.answer` directly: it calls `chamber.answer` itself, then immediately forwards the answer
content to the Warden that owns the now-resumed task (`Task.warden_id`, which placement fixed for
the task's whole life this phase), tagged with `AnswerInput.wire_question_id` -- the *original*
wire `Question.question_id`, never the chamber's own id -- because
`hivemind.wardens.ticks.questions.forward_answer` matches an incoming `Answer` back to its own
blocked sub-bee by that exact original id (`warden._questions`'s own key), not by anything the
Brood Chamber ever sees; and with `AnswerInput.correlation_id` set to the Question's own arrival
envelope id, because `waggle.envelope.wrap` refuses to build a `supervision.answer` Envelope (a
`MessageShape.REPLY`) without one, exactly the reason `hivemind.wardens.ticks.questions.
forward_question` remembers `_question_envelope_ids` for the hop below this one. `route_answers`
is what its own name in the dispatch map describes as a *sweep*, kept as a light reconciliation:
it drops bookkeeping for any question this Queen asked whose task has since left `BLOCKED` by some
other path (a withdrawal), so `tracked` never grows unbounded.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen.Queen`'s tick (`handle_question`, `route_answers`) and by whichever
    composition root wires `hive inbox answer` to the Queen (`answer_question`). Calls into
    `hivemind.brood_chamber` (Answer, AnswerSource, Task, TaskStatus), `hivemind.cell`
    (HoneyClearance), `hivemind.queen.deps` (QueenDeps, WardenLink) and waggle only.

Key invariants:
    - `answer_question` sends the wire Answer only when the resumed task's own `warden_id` still
      names an attached Warden; a task whose Warden has since detached is resumed in the chamber
      regardless (the human's answer is never lost), but nothing is sent over a link that no
      longer exists.
    - The wire `Answer.question_id` `answer_question` sends is always the *original* wire
      `Question.question_id` (`AnswerInput.wire_question_id`, falling back to
      `AnswerInput.question_id` only when the caller never learned a different one), never the
      Brood Chamber's own internal `Question.id`: a Warden matches an Answer back to its own
      blocked sub-bee by that original id alone.
    - `route_answers` never re-sends an Answer: forwarding happens exactly once, inside
      `answer_question`, at the moment the human's answer is recorded.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the Question/Answer flow this module
      implements.
    - hivemind.brood_chamber.chamber for BroodChamber's own public API, the boundary this
      module's docstring explains working around.
    - waggle.messages.supervision.questions for the wire Question/Answer this module builds from
      and to.
"""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass

from hivemind.brood_chamber import Answer, AnswerSource, Question, Task, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.queen.deps import QueenDeps, WardenLink
from waggle.envelope import wrap
from waggle.ids import MessageId, TaskId, WardenId
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Answer as WireAnswer
from waggle.messages.supervision import AnswerSource as WireAnswerSource
from waggle.messages.supervision import Question as WireQuestion

__all__ = ["AnswerInput", "answer_question", "handle_question", "route_answers"]


@dataclass(frozen=True, slots=True)
class AnswerInput:
    """What to answer with, grouped so `answer_question` stays within codingrules 5.1's limit.

    Attributes:
        question_id: The question being answered, by the Brood Chamber's own internal id (what
            `pending_questions` lists, and what a human picks from).
        text: The answer text.
        source: Who answered; QUEEN by default (a HUMAN answer must be C2).
        clearance: The answer's data-sensitivity label; C1 by default.
        wire_question_id: The *original* wire `Question.question_id`, forwarded back verbatim so
            the asking Warden can match this Answer to the right blocked sub-bee (module
            docstring); falls back to `question_id` when the caller never learned a different one.
        correlation_id: The envelope id of the Question this Answer replies to; `Answer` is a
            `waggle.envelope.MessageShape.REPLY` (mirrors `hivemind.wardens.ticks.questions.
            forward_question`'s own `_question_envelope_ids`), so `wrap` rejects sending it with
            none set. None only when the caller never learned one (nothing is sent in that case;
            see `answer_question`'s own body).
    """

    question_id: MessageId
    text: str
    source: AnswerSource = AnswerSource.QUEEN
    clearance: HoneyClearance = HoneyClearance.C1
    wire_question_id: MessageId | None = None
    correlation_id: MessageId | None = None


async def handle_question(deps: QueenDeps, question: WireQuestion) -> Question:
    """Move `question`'s task RUNNING -> BLOCKED, visible from then on in the human inbox.

    Args:
        deps: The Queen's collaborators.
        question: The Warden-forwarded Question; `question.asked_by` names the original asker
            (a Worker or a Warden), which survives forwarding on the wire.

    Returns:
        The Brood Chamber's own newly-minted Question -- a different id than `question.question_id`
        (module docstring); the caller must remember both to answer correctly.
    """
    return await deps.chamber.ask(
        question.task_id,
        asked_by=question.asked_by,
        text=question.text,
        options=question.options,
        clearance=HoneyClearance.from_wire(question.clearance),
    )


async def answer_question(
    deps: QueenDeps, wardens: Sequence[WardenLink], answer_input: AnswerInput
) -> Task:
    """Record an answer in the chamber, then forward it to the Warden that owns the task.

    The one path a caller should use to answer a pending Question (module docstring): it both
    resumes the task in the Brood Chamber and forwards the same content over the wire, since
    nothing can read an already-recorded Answer back out afterwards.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; the resumed task's own `warden_id` picks one.
        answer_input: The question being answered, its text, source and clearance.

    Returns:
        The question's task, now RUNNING again.
    """
    answer = Answer(
        text=answer_input.text,
        chosen_option=None,
        source=answer_input.source,
        clearance=answer_input.clearance,
        answered_at=deps.clock.now(),
    )
    task = await deps.chamber.answer(answer_input.question_id, answer)
    link = _link_for(wardens, task.warden_id)
    # `Answer` is a reply (module docstring): with no envelope to correlate to (the Question's own
    # arrival was never tracked, or its Warden has since detached), the human's answer is still
    # recorded above, but nothing is sent over a link `wrap` would refuse to build an Envelope for.
    if link is not None and answer_input.correlation_id is not None:
        wire_question_id = answer_input.wire_question_id or answer_input.question_id
        wire = _to_wire_answer(wire_question_id, task.id, answer)
        envelope = wrap(
            wire, link.hop, clock=deps.clock, correlation_id=answer_input.correlation_id
        )
        await link.transport.send(envelope)
    return task


async def route_answers(deps: QueenDeps, tracked: MutableMapping[MessageId, TaskId]) -> None:
    """Drop bookkeeping for any tracked question whose task has left BLOCKED by another path.

    Args:
        deps: The Queen's collaborators.
        tracked: question_id -> task_id, populated by whatever recorded `handle_question`'s own
            `chamber.ask` call; mutated in place.
    """
    for question_id, task_id in tuple(tracked.items()):
        task = await deps.chamber.get(task_id)
        if task.status is not TaskStatus.BLOCKED:
            tracked.pop(question_id, None)


def _link_for(wardens: Sequence[WardenLink], warden_id: WardenId | None) -> WardenLink | None:
    """Return the WardenLink named by `warden_id`, or None when it names no attached Warden."""
    return next((link for link in wardens if link.warden_id == warden_id), None)


def _to_wire_answer(question_id: MessageId, task_id: TaskId, answer: Answer) -> WireAnswer:
    """Convert a hivemind Answer into the wire form a Warden's own link carries."""
    return WireAnswer(
        question_id=question_id,
        task_id=task_id,
        text=answer.text,
        chosen_option=answer.chosen_option,
        source=WireAnswerSource(answer.source.value),
        clearance=WireHoneyClearance(answer.clearance.value),
    )
