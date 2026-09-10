"""Define handle_question, answer_question and sync_answers_from_chamber: Queen question traffic.

Roadmap step 3.20's own dispatch map: "Question from a sub-bee -> BLOCK_ON_QUESTION: chamber.ask
(task BLOCKED, pending_question_id) -> visible in the human inbox; Answer arrives via chamber.answer
(the CLI's `hive inbox answer`): questions.sync_answers_from_chamber finds answered questions the
Queen has not yet forwarded and sends an Answer envelope to the Warden that asked."

`handle_question` is the first half: `chamber.ask` with the *original* asker (`Question.asked_by`
survives forwarding on the wire, so the Warden's own name never overwrites who actually asked).
It returns the chamber's own newly-minted `Question`, which never reuses the wire message's own
`question_id` (`hivemind.brood_chamber.questions`'s own module docstring: the chamber's `Question`
is "a plain stored value with its own status machine", not the wire form, so `chamber.ask` always
mints a fresh id `pending_questions` can read back after a restart) -- so a caller must remember
both ids, plus the arriving envelope's own id (see below), to answer correctly (`queen.
answer_question`'s own `_question_wire_ids`/`_question_envelope_ids`).

There are exactly two places an Answer is ever forwarded, and exactly one dict-popping rule each
(this dispatch's own fix 3 -- see below for what it replaced): `answer_question`, the in-process
path `Queen.answer_question` calls directly, and `sync_answers_from_chamber`'s own
`_forward_from_note`, the cross-process path both `Queen`'s own tick and `hive run`'s poll loop
call. `answer_question` exists because `hivemind.brood_chamber.BroodChamber`'s own public API
exposes no way to read an already-`ANSWERED` `Question`'s content back out (no `get_question`, and
`pending_questions` filters to `ASKED` only -- `brood_chamber/**` is outside this dispatch's owned
files, so no method could be added there to close that gap): it calls `chamber.answer` itself, then
immediately forwards the answer content to the Warden that owns the now-resumed task (`Task.
warden_id`, which placement fixed for the task's whole life this phase), tagged with `AnswerInput.
wire_question_id` -- the *original* wire `Question.question_id`, never the chamber's own id --
because `hivemind.wardens.ticks.questions.forward_answer` matches an incoming `Answer` back to its
own blocked sub-bee by that exact original id (`warden._questions`'s own key), not by anything the
Brood Chamber ever sees; and with `AnswerInput.correlation_id` set to the Question's own arrival
envelope id, because `waggle.envelope.wrap` refuses to build a `supervision.answer` Envelope (a
`MessageShape.REPLY`) without one, exactly the reason `hivemind.wardens.ticks.questions.
forward_question` remembers `_question_envelope_ids` for the hop below this one. `Queen.
answer_question` -- the method that calls this function -- also drops this question's own
`_open_questions` entry itself, in the same call (this function has no `Queen` to pop it from;
only `QueenDeps`, `Sequence[WardenLink]` and the answer itself): nothing else may race it, since a
caller calls it once, synchronously, to answer one question, so there is nothing to coordinate.

`sync_answers_from_chamber` (roadmap step 3.21, second half) is the *other* forwarding path, for a
*second* process: `hive inbox answer` (its own CLI command) runs in a separate process from a
running `hive run`, so it cannot call `Queen.answer_question` directly (v0 has no live link into
the Queen process, `hivemind.cli.compose`'s own module docstring) -- it can only call
`chamber.answer` on the same SQLite file, which resumes the task in the Brood Chamber but forwards
nothing, since forwarding lives only inside the two functions named above. The Brood Chamber's own
public API still exposes no way to read that recorded `Answer`'s text back out (this module's own
docstring, above), so `hive inbox answer` also writes a `hivemind.memory.Note` -- keyed by
`answer_note_author(question_id)`, a plain `MemoryStore.list_notes(author=...)` filter -- carrying
the answer text and clearance a *separate* process can still reach, since `hivemind.memory` (unlike
the Brood Chamber) is a store this module already holds a handle to (`QueenDeps.memory`).
`sync_answers_from_chamber` walks this Queen's own `_question_wire_ids` bookkeeping -- keyed by the
Brood Chamber's own Question id, the same id `answer_note_author` keys a Note by, unlike
`_open_questions`, which is keyed by the *wire* `Question.question_id` instead -- and for every one
whose task has left `BLOCKED`, looks for that Note: found one -> forward it exactly like
`answer_question` would, tagged with the same `wire_question_id`/`correlation_id` bookkeeping, and
only then drop `_open_questions`/`_question_wire_ids`/`_question_envelope_ids` together. Found
none -> retry: every one of this question's own dicts is left exactly as it was, so the very next
call looks again -- this covers both "the Note has not landed yet" (`hive inbox answer`'s own two
writes are not atomic, so a call landing between them must not give up) and "no Note is ever coming"
(`answer_question` already forwarded this one itself and popped `_open_questions`, so `task_id is
None` below short-circuits without a Note lookup at all). It takes the whole `Queen` (an exception
to this module's own convention of taking explicit `deps`/`wardens` parameters, matching
`hivemind.queen.ticks.alarms`/`.results`) because `_open_questions` and its sibling id-tracking
dicts are private `Queen` instance state with no public accessor, and adding one is outside this
dispatch's owned files.

Before this dispatch, `Queen`'s own tick called a separate `route_answers` sweep instead of this
function: it dropped `_open_questions` for *any* tracked question whose task had left BLOCKED, for
any reason, which is exactly wrong for the cross-process case above -- a tick landing between
`hive inbox answer`'s two writes saw the task already off BLOCKED (the first write alone moves it)
and dropped `_open_questions` right there, so the next `sync_answers_from_chamber` poll found its
own `_question_wire_ids` entry orphaned and read that, wrongly, as "already forwarded by some other
path," dropping its own tracking too and losing the answer for good. `sync_answers_from_chamber`
is now the *only* place this path's own tracking is ever dropped, called by both the tick and
`hive run`'s poll loop, so there is exactly one rule -- "keep tracking until the Note is present,
then forward once" -- and exactly one place it is applied.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.queen.Queen`'s tick (`handle_question`, `sync_answers_from_chamber`) and by
    whichever composition root wires `hive inbox answer` to the Queen (`answer_question`);
    `hivemind.cli.compose.run_goal`'s own poll loop also calls `sync_answers_from_chamber`, for the
    cross-process reason given above. Calls into `hivemind.brood_chamber` (Answer, AnswerSource,
    Task, TaskStatus), `hivemind.cell` (HoneyClearance), `hivemind.memory` (MemoryStore, Note),
    `hivemind.queen.deps` (QueenDeps, WardenLink) and waggle only.

Key invariants:
    - `answer_question` sends the wire Answer only when the resumed task's own `warden_id` still
      names an attached Warden; a task whose Warden has since detached is resumed in the chamber
      regardless (the human's answer is never lost), but nothing is sent over a link that no
      longer exists.
    - The wire `Answer.question_id` both forwarding paths send is always the *original* wire
      `Question.question_id` (`AnswerInput.wire_question_id`, falling back to
      `AnswerInput.question_id` only when the caller never learned a different one), never the
      Brood Chamber's own internal `Question.id`: a Warden matches an Answer back to its own
      blocked sub-bee by that original id alone.
    - An Answer is forwarded exactly once per question: `answer_question` and
      `sync_answers_from_chamber` each pop `_question_wire_ids` (directly, or through
      `_open_questions` being gone) for the question they just forwarded, so the other path can
      never find it again (this dispatch's own fix 3).
    - `sync_answers_from_chamber` never forwards twice for the same question: its own three dicts
      are only ever popped together, in the same call, and only once a matching Note is actually
      forwarded. A question resolved by something other than `hive inbox answer` or `Queen.
      answer_question` (a withdrawal, not wired to anything in this phase) would retry forever --
      an accepted v0 gap (flagged in this dispatch's own report), since nothing in phase 3
      produces that case.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the Question/Answer flow this module
      implements.
    - hivemind.brood_chamber.chamber for BroodChamber's own public API, the boundary this
      module's docstring explains working around.
    - hivemind.memory.store.protocol for MemoryStore.list_notes, the read `sync_answers_from_
      chamber` uses in place of a Brood Chamber method that does not exist.
    - waggle.messages.supervision.questions for the wire Question/Answer this module builds from
      and to.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Answer, AnswerSource, Question, Task, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.memory import MemoryStore, Note
from hivemind.queen.deps import QueenDeps, WardenLink
from waggle.envelope import wrap
from waggle.ids import MessageId, TaskId, WardenId
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Answer as WireAnswer
from waggle.messages.supervision import AnswerSource as WireAnswerSource
from waggle.messages.supervision import Question as WireQuestion

if TYPE_CHECKING:
    # Only for the type hint below: hivemind.queen.queen imports this module at load time (its
    # own `from hivemind.queen import questions`), so a real-time import here would cycle.
    from hivemind.queen.queen import Queen

# hive inbox answer's own Note.author prefix (module docstring); sync_answers_from_chamber's
# MemoryStore.list_notes(author=...) filter looks for exactly this, so both sides of the
# cross-process handoff share one constant rather than two copies of the same string shape.
ANSWER_NOTE_AUTHOR_PREFIX = "human_answer:"

__all__ = [
    "ANSWER_NOTE_AUTHOR_PREFIX",
    "AnswerInput",
    "answer_note_author",
    "answer_question",
    "handle_question",
    "sync_answers_from_chamber",
]


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


def answer_note_author(question_id: MessageId) -> str:
    """Build the `Note.author` key `hive inbox answer` writes and this module reads back.

    Args:
        question_id: The Brood Chamber's own Question id (what `pending_questions` lists, and
            what a human answers by).

    Returns:
        `ANSWER_NOTE_AUTHOR_PREFIX` followed by `question_id`, always well under `hivemind.memory.
        notes.MAX_NOTE_AUTHOR_CHARS`.
    """
    return f"{ANSWER_NOTE_AUTHOR_PREFIX}{question_id}"


async def sync_answers_from_chamber(queen: Queen) -> int:
    """Forward every human answer `hive inbox answer` recorded that this Queen has not sent yet.

    See the module docstring for why this exists (a second, answering process cannot call
    `answer_question` directly) and why it takes the whole `Queen` rather than explicit `deps`/
    `wardens` parameters. Walks `_question_wire_ids` (keyed by the Brood Chamber's own Question
    id -- what `hive inbox answer`'s own `question_id` argument names, and so what
    `answer_note_author` keys its Note by) rather than `_open_questions` (keyed by the *wire*
    `Question.question_id`, `Queen._act`'s own `BLOCK_ON_QUESTION` handling): the two dicts are
    populated together but keyed in opposite spaces, and both the Note lookup and the reply
    envelope's own `correlation_id` (`_question_envelope_ids`, keyed the same way) need the
    chamber id, not the wire one.

    Args:
        queen: The running Queen; its own `_open_questions`/`_question_wire_ids`/
            `_question_envelope_ids` bookkeeping is walked and mutated.

    Returns:
        How many answers were actually forwarded this call (0 when every tracked question is
        still BLOCKED, or was resolved with no matching Note to forward).
    """
    forwarded = 0
    # A snapshot: this loop pops from every one of the three dicts as it goes, so iterating one
    # of them while mutating it would raise.
    for chamber_question_id, wire_question_id in tuple(queen._question_wire_ids.items()):
        task_id = queen._open_questions.get(wire_question_id)
        if task_id is None:
            # Already forwarded by the in-process path (Queen.answer_question pops
            # _open_questions itself, module docstring's own fix 3): drop this dict's own
            # now-stale entry too, rather than ever looking for a Note that will never exist.
            queen._question_wire_ids.pop(chamber_question_id, None)
            queen._question_envelope_ids.pop(chamber_question_id, None)
            continue
        task = await queen._deps.chamber.get(task_id)
        if task.status is TaskStatus.BLOCKED:
            continue  # Still waiting on an answer; nothing to do for this one yet.
        if not await _forward_from_note(queen, task, chamber_question_id, wire_question_id):
            # This dispatch's own fix 4b: `hive inbox answer` writes `chamber.answer()` (which is
            # what moved `task.status` off BLOCKED, just observed above) and its own answer Note
            # on two separate store connections, not atomically -- a poll landing between the two
            # writes finds no Note yet. Dropping this question's tracking here, unconditionally,
            # is exactly what used to lose the answer for good: the next poll would never look
            # again. Leaving every one of this question's own dicts untouched means the very next
            # sync retries the same Note lookup, which succeeds once the second write lands.
            continue
        forwarded += 1
        queen._open_questions.pop(wire_question_id, None)
        queen._question_wire_ids.pop(chamber_question_id, None)
        queen._question_envelope_ids.pop(chamber_question_id, None)
    return forwarded


async def _forward_from_note(
    queen: Queen, task: Task, chamber_question_id: MessageId, wire_question_id: MessageId
) -> bool:
    """Forward `chamber_question_id`'s answer Note to `task`'s own Warden, if one was left."""
    correlation_id = queen._question_envelope_ids.get(chamber_question_id)
    note = await _find_answer_note(queen._deps.memory, chamber_question_id)
    link = _link_for(queen.wardens, task.warden_id)
    if note is None or link is None or correlation_id is None:
        # No Note (answered some other way), no attached Warden, or no envelope to correlate to
        # (module docstring's own "Answer is a reply" rule, mirrored from answer_question): the
        # human's answer, if any, is not lost -- chamber.answer already recorded it -- but there
        # is nothing more this sync can do.
        return False
    answer = Answer(
        text=note.text,
        chosen_option=None,
        source=AnswerSource.HUMAN,
        clearance=note.clearance,
        answered_at=queen._deps.clock.now(),
    )
    wire = _to_wire_answer(wire_question_id, task.id, answer)
    envelope = wrap(wire, link.hop, clock=queen._deps.clock, correlation_id=correlation_id)
    await link.transport.send(envelope)
    return True


async def _find_answer_note(memory: MemoryStore, question_id: MessageId) -> Note | None:
    """Return `question_id`'s answer Note, or None when `hive inbox answer` never wrote one."""
    # C2 (the widest allowance): sync_answers_from_chamber is a Queen-side, not human-facing,
    # read -- the same rule handle_question already applies when it clears a Question's own
    # clearance ceiling for the trigger event it builds around one.
    notes = await memory.list_notes(answer_note_author(question_id), HoneyClearance.C2, 1)
    return notes[0] if notes else None


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
