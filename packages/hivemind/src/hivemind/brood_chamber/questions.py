"""Define Question and Answer: the Brood Chamber's blocking-question domain models.

A Worker or a Warden that cannot proceed without more information raises a Question against its
task; the task moves to `TaskStatus.BLOCKED` (`hivemind.brood_chamber.task_state`) until an
`Answer` comes back, at which point it moves back to `RUNNING`. This module holds the domain form
of both, stored with the task (`hivemind.brood_chamber.store`, roadmap step 2.6) rather than as a
Waggle message: `waggle.messages.supervision.questions.Question`/`Answer` are what travels the wire
between a bee and the Queen, but once the Queen has one it is recorded here so the Queen's inbox can
read pending questions back after a restart. The two shapes mirror each other in spirit (same
fields, same HUMAN-answer-implies-C2 rule) rather than sharing a class, because the wire form is a
`WaggleMessage` (registered under a kind, correlation rules apply) and this one is a plain stored
value with its own status machine (`QuestionStatus`) the wire form does not need.

`QUESTION_TRANSITIONS` is the second machine of the two Appendix C fixes for the Brood Chamber (the
"Question" row): `ASKED -> ANSWERED / WITHDRAWN`, each edge tested, forbidden edges raising, exactly
like `hivemind.brood_chamber.task_state.TRANSITIONS`. The two tables never call each other: a
Question's status and its Task's status change together (asking blocks the task, answering resumes
it) but that coupling is `hivemind.brood_chamber.chamber`'s job (roadmap step 2.8), not this
module's.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by `hivemind.brood_chamber.chamber`
    (roadmap step 2.8) from a Worker's or Warden's request, stored by
    `hivemind.brood_chamber.store` (roadmap step 2.6) alongside the Task it blocks, and read by the
    Queen's inbox for pending questions. Calls into `hivemind.brood_chamber.errors` and
    `hivemind.cell` (for `HoneyClearance`) only.

Key invariants:
    - QUESTION_TRANSITIONS has exactly one entry per QuestionStatus member; ANSWERED and WITHDRAWN
      map to no further edges, so an answered or withdrawn Question never changes status again.
    - A Question's `answer` is set if and only if its `status` is ANSWERED.
    - An Answer whose `chosen_option` is set is only valid on a Question whose `options` is long
      enough to contain that index; Question's own validator checks this, because Answer alone
      does not know how many options its Question offered.
    - AnswerSource's member names and values are identical to
      `waggle.messages.supervision.AnswerSource`'s (tests/unit/brood_chamber/test_questions.py
      checks it member for member).
    - An Answer from AnswerSource.HUMAN always carries HoneyClearance.C2: a human's words are
      personal by provenance, whatever they say (mirrors waggle's own Answer rule).

See Also:
    - .claude/codingrules.md Appendix C, "Question" row, for the transition table this module
      implements.
    - waggle.messages.supervision.questions for the wire Question/Answer this module mirrors.
    - hivemind.brood_chamber.task_state for TaskStatus.BLOCKED, the Task-side half of asking.
    - hivemind.brood_chamber.errors for InvalidTransitionError, the error
      assert_question_transition raises.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.cell import HoneyClearance
from waggle.ids import IdKind, WardenId, WorkerId
from waggle.messages.base import MessageIdField, TaskIdField, UtcDatetime, id_validator

MIN_TEXT_CHARS = 1  # A question, or its answer, always says something.
MAX_TEXT_CHARS = 4_000  # A few paragraphs: enough to ask or answer properly, never a document.
MAX_OPTIONS = 16  # Closed choices the asker offers; more than this is a form, not a question.
MAX_OPTION_CHARS = 200  # One choice is a sentence.

__all__ = [
    "MAX_OPTIONS",
    "MAX_OPTION_CHARS",
    "MAX_TEXT_CHARS",
    "MIN_TEXT_CHARS",
    "QUESTION_TRANSITIONS",
    "Answer",
    "AnswerSource",
    "Question",
    "QuestionStatus",
    "assert_question_transition",
]


class QuestionStatus(Enum):
    """Every state a Question can be in, from being asked to being resolved.

    See QUESTION_TRANSITIONS below for the legal moves between these.
    """

    ASKED = "ASKED"  # Raised; the task it belongs to is BLOCKED on it.
    ANSWERED = "ANSWERED"  # Terminal: an Answer arrived; the task resumes.
    WITHDRAWN = "WITHDRAWN"  # Terminal: withdrawn without an answer; the task resumes anyway.


class AnswerSource(Enum):
    """Who answered a Question; mirrors `waggle.messages.supervision.AnswerSource`.

    The human is not a bee address, so this says when an answer came from them rather than from a
    bee acting on their behalf.
    """

    HUMAN = "HUMAN"  # Relayed by the Queen; the answer is C2 by provenance (see Answer below).
    QUEEN = "QUEEN"  # The Queen answered without asking the human.
    WARDEN = "WARDEN"  # A Warden answered from what it already knew.


# The single transition table (codingrules section 9) for QuestionStatus: one entry per member,
# each edge commented with what causes it. ANSWERED and WITHDRAWN are terminal.
QUESTION_TRANSITIONS: Mapping[QuestionStatus, frozenset[QuestionStatus]] = {
    QuestionStatus.ASKED: frozenset(
        {
            QuestionStatus.ANSWERED,  # an Answer arrived
            QuestionStatus.WITHDRAWN,  # the task was cancelled, or the asker no longer needs it
        }
    ),
    QuestionStatus.ANSWERED: frozenset(),  # terminal: nothing follows
    QuestionStatus.WITHDRAWN: frozenset(),  # terminal: nothing follows
}


def assert_question_transition(
    from_status: QuestionStatus, to_status: QuestionStatus, question_id: str | None = None
) -> None:
    """Raise unless QUESTION_TRANSITIONS allows moving from `from_status` to `to_status`.

    Args:
        from_status: The question's current status.
        to_status: The status a caller wants to move it to.
        question_id: The question's id, when the caller has it, folded into the error message.

    Raises:
        InvalidTransitionError: `to_status` is not one of the edges QUESTION_TRANSITIONS lists for
            `from_status`, for instance answering an already-ANSWERED question.
    """
    if to_status not in QUESTION_TRANSITIONS[from_status]:
        raise InvalidTransitionError(from_status, to_status, subject_id=question_id)


# A bee that can answer on the Queen's or a Warden's behalf: never the Queen itself (it has no
# waggle id) and never a device, mirroring waggle.messages.supervision.questions's own _BeeId.
_AskerId = Annotated[WorkerId | WardenId, id_validator(IdKind.WORKER, IdKind.WARDEN)]


class Answer(BaseModel):
    """One answer to a Question, embedded in it once `status` is ANSWERED.

    Carries no `question_id`: unlike the wire form (`waggle.messages.supervision.questions.
    Answer`), this one only ever exists nested inside the `Question` it answers, so repeating the
    id would just be a redundant field two consistency checks would have to keep in sync.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(
        min_length=MIN_TEXT_CHARS, max_length=MAX_TEXT_CHARS, description="The answer."
    )
    chosen_option: int | None = Field(
        default=None,
        ge=0,
        description="Index into the Question's options, when one was chosen; the Question "
        "validates it is in range for that Question's own options.",
    )
    source: AnswerSource = Field(description="Who answered: the human, the Queen, or a Warden.")
    clearance: HoneyClearance = Field(
        description="The answer's data-sensitivity label; HUMAN answers are always C2."
    )
    answered_at: UtcDatetime = Field(description="When the answer was given.")

    @model_validator(mode="after")
    def _human_answer_is_c2(self) -> Answer:
        """Require HoneyClearance.C2 on an answer whose source is HUMAN."""
        # A human's words are personal by provenance, whatever they say; a lower label would let
        # a program launder them into a tier that must never hold them (mirrors waggle's rule).
        if self.source is AnswerSource.HUMAN and self.clearance is not HoneyClearance.C2:
            raise ValueError(
                f"Answer from source HUMAN requires clearance C2, got {self.clearance.name}."
            )
        return self


class Question(BaseModel):
    """A question raised against a task, blocking it until it is ANSWERED or WITHDRAWN.

    Stored with the task it belongs to (`hivemind.brood_chamber.store`, roadmap step 2.6); the
    Queen's inbox lists every Question with `status == ASKED` across every task as its pending
    questions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: MessageIdField = Field(
        description="This question's own id, a MessageId minted by the asker with new_message_id "
        "(docs/waggle/spec.md 'Identity and ids' item 1; there is no IdKind.QUESTION)."
    )
    task_id: TaskIdField = Field(description="The task this question blocks.")
    asked_by: _AskerId = Field(description="The Worker or Warden that asked.")
    text: str = Field(
        min_length=MIN_TEXT_CHARS, max_length=MAX_TEXT_CHARS, description="The question."
    )
    options: tuple[Annotated[str, Field(max_length=MAX_OPTION_CHARS)], ...] = Field(
        default=(),
        max_length=MAX_OPTIONS,
        description="Closed choices the asker can offer; empty means the answer is free text.",
    )
    clearance: HoneyClearance = Field(
        description="The question text's data-sensitivity label, since it may quote Real Cell or "
        "personal data."
    )
    asked_at: UtcDatetime = Field(description="When the question was first asked.")
    status: QuestionStatus = Field(
        default=QuestionStatus.ASKED, description="Where this question is in QUESTION_TRANSITIONS."
    )
    answer: Answer | None = Field(
        default=None, description="Set once status is ANSWERED; None otherwise."
    )

    @model_validator(mode="after")
    def _validate_answer_consistency(self) -> Question:
        """Run every cross-field invariant; split into helpers so each stays small and readable."""
        _check_answer_matches_status(self)
        _check_chosen_option_in_range(self)
        _check_answered_at_after_asked_at(self)
        return self


def _check_answer_matches_status(question: Question) -> None:
    """Require `answer` set if and only if `status` is ANSWERED."""
    if question.status is QuestionStatus.ANSWERED and question.answer is None:
        raise ValueError(f"Question {question.id} is ANSWERED and requires an answer.")
    if question.status is not QuestionStatus.ANSWERED and question.answer is not None:
        raise ValueError(
            f"Question {question.id} is {question.status.name}, not ANSWERED, and must not "
            "carry an answer."
        )


def _check_chosen_option_in_range(question: Question) -> None:
    """Reject a chosen_option index that is not valid for this Question's own options."""
    if question.answer is None or question.answer.chosen_option is None:
        return
    if question.answer.chosen_option >= len(question.options):
        raise ValueError(
            f"Question {question.id} answer.chosen_option "
            f"({question.answer.chosen_option}) is out of range for {len(question.options)} "
            "options."
        )


def _check_answered_at_after_asked_at(question: Question) -> None:
    """Reject an answer timestamped before the question was asked."""
    if question.answer is not None and question.answer.answered_at < question.asked_at:
        raise ValueError(
            f"Question {question.id} answer.answered_at ({question.answer.answered_at}) "
            f"precedes asked_at ({question.asked_at})."
        )
