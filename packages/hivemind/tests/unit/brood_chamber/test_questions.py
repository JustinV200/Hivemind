"""Tests for hivemind.brood_chamber.questions: QuestionStatus, AnswerSource, Answer, Question.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/questions.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.questions for the module under test.
"""

from __future__ import annotations

import itertools

import pytest
from builders.tasks import make_answer, make_question
from pydantic import ValidationError

from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.questions import (
    QUESTION_TRANSITIONS,
    Answer,
    AnswerSource,
    Question,
    QuestionStatus,
    assert_question_transition,
)
from hivemind.cell import HoneyClearance
from waggle.clock import FakeClock
from waggle.ids import new_message_id, new_task_id, new_warden_id, new_worker_id
from waggle.messages.supervision import AnswerSource as WireAnswerSource

# ──────────────────────────────────────────────────────────────────────────────
# AnswerSource mirrors the wire enum
# ──────────────────────────────────────────────────────────────────────────────


def test_answer_source_mirrors_the_wire_enum_member_for_member() -> None:
    assert [member.name for member in AnswerSource] == [member.name for member in WireAnswerSource]
    assert [member.value for member in AnswerSource] == [
        member.value for member in WireAnswerSource
    ]


# ──────────────────────────────────────────────────────────────────────────────
# QUESTION_TRANSITIONS: walk every edge, assert every non-edge raises
# ──────────────────────────────────────────────────────────────────────────────

_ALLOWED_EDGES = [
    (status, target) for status, targets in QUESTION_TRANSITIONS.items() for target in targets
]
_ALL_PAIRS = list(itertools.product(QuestionStatus, QuestionStatus))
_FORBIDDEN_EDGES = [pair for pair in _ALL_PAIRS if pair not in _ALLOWED_EDGES]


def test_question_transitions_has_exactly_one_entry_per_question_status() -> None:
    assert set(QUESTION_TRANSITIONS.keys()) == set(QuestionStatus)


@pytest.mark.parametrize(("from_status", "to_status"), _ALLOWED_EDGES)
def test_assert_question_transition_does_not_raise_on_every_allowed_edge(
    from_status: QuestionStatus, to_status: QuestionStatus
) -> None:
    assert_question_transition(from_status, to_status)


@pytest.mark.parametrize(("from_status", "to_status"), _FORBIDDEN_EDGES)
def test_assert_question_transition_raises_on_every_pair_outside_the_table(
    from_status: QuestionStatus, to_status: QuestionStatus
) -> None:
    with pytest.raises(InvalidTransitionError):
        assert_question_transition(from_status, to_status)


def test_assert_question_transition_error_carries_the_question_id_when_given() -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        assert_question_transition(
            QuestionStatus.ANSWERED, QuestionStatus.WITHDRAWN, question_id="msg_abc"
        )

    assert excinfo.value.subject_id == "msg_abc"


# ──────────────────────────────────────────────────────────────────────────────
# Answer
# ──────────────────────────────────────────────────────────────────────────────


def test_answer_accepts_a_non_human_source_at_a_lower_clearance() -> None:
    answer = make_answer(source=AnswerSource.WARDEN, clearance=HoneyClearance.C0)

    assert answer.clearance is HoneyClearance.C0


def test_answer_accepts_a_human_source_at_c2() -> None:
    answer = make_answer(source=AnswerSource.HUMAN, clearance=HoneyClearance.C2)

    assert answer.source is AnswerSource.HUMAN


@pytest.mark.parametrize("clearance", [HoneyClearance.C0, HoneyClearance.C1])
def test_answer_rejects_a_human_source_below_c2(clearance: HoneyClearance) -> None:
    with pytest.raises(ValidationError, match="HUMAN"):
        make_answer(source=AnswerSource.HUMAN, clearance=clearance)


def test_answer_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Answer.model_validate(
            {
                "text": "ok",
                "chosen_option": None,
                "source": "QUEEN",
                "clearance": "C1",
                "answered_at": FakeClock().now().isoformat(),
                "extra": "nope",
            }
        )


def test_answer_is_frozen() -> None:
    answer = make_answer()

    with pytest.raises(ValidationError, match="frozen"):
        answer.text = "changed"  # type: ignore[misc]  # The assignment is the test.


def test_answer_json_round_trips() -> None:
    original = make_answer(chosen_option=1, source=AnswerSource.WARDEN)

    restored = Answer.model_validate_json(original.model_dump_json())

    assert restored == original


# ──────────────────────────────────────────────────────────────────────────────
# Question: answer <-> status ANSWERED
# ──────────────────────────────────────────────────────────────────────────────


def test_question_defaults_to_asked_with_no_answer() -> None:
    question = make_question()

    assert question.status is QuestionStatus.ASKED
    assert question.answer is None


def test_question_accepts_answered_status_with_an_answer() -> None:
    question = make_question(status=QuestionStatus.ANSWERED, answer=make_answer())

    assert question.status is QuestionStatus.ANSWERED
    assert question.answer is not None


def test_question_rejects_answered_status_with_no_answer() -> None:
    with pytest.raises(ValidationError, match="ANSWERED"):
        make_question(status=QuestionStatus.ANSWERED, answer=None)


@pytest.mark.parametrize("status", [QuestionStatus.ASKED, QuestionStatus.WITHDRAWN])
def test_question_rejects_a_non_answered_status_that_carries_an_answer(
    status: QuestionStatus,
) -> None:
    with pytest.raises(ValidationError, match="ANSWERED"):
        make_question(status=status, answer=make_answer())


# ──────────────────────────────────────────────────────────────────────────────
# Question: chosen_option range
# ──────────────────────────────────────────────────────────────────────────────


def test_question_accepts_a_chosen_option_in_range() -> None:
    question = make_question(
        options=("a", "b", "c"),
        status=QuestionStatus.ANSWERED,
        answer=make_answer(chosen_option=2),
    )

    assert question.answer is not None
    assert question.answer.chosen_option == 2


def test_question_accepts_a_null_chosen_option_with_no_options() -> None:
    question = make_question(
        options=(), status=QuestionStatus.ANSWERED, answer=make_answer(chosen_option=None)
    )

    assert question.answer is not None
    assert question.answer.chosen_option is None


def test_question_rejects_a_chosen_option_out_of_range() -> None:
    with pytest.raises(ValidationError, match="out of range"):
        make_question(
            options=("a", "b"),
            status=QuestionStatus.ANSWERED,
            answer=make_answer(chosen_option=2),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Question: answered_at >= asked_at
# ──────────────────────────────────────────────────────────────────────────────


def test_question_accepts_answered_at_equal_to_asked_at() -> None:
    clock = FakeClock()
    at = clock.now()

    question = make_question(
        asked_at=at,
        status=QuestionStatus.ANSWERED,
        answer=make_answer(answered_at=at),
    )

    assert question.answer is not None


def test_question_rejects_answered_at_before_asked_at() -> None:
    clock = FakeClock()
    early = clock.now()
    clock.advance(10)
    later = clock.now()

    with pytest.raises(ValidationError, match="precedes"):
        make_question(
            asked_at=later,
            status=QuestionStatus.ANSWERED,
            answer=make_answer(answered_at=early),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Question: asked_by, options, frozen, unknown field, JSON round trip
# ──────────────────────────────────────────────────────────────────────────────


def test_question_accepts_a_warden_as_asked_by() -> None:
    clock = FakeClock()

    question = make_question(clock=clock, asked_by=new_warden_id(clock))

    assert question.asked_by.startswith("warden_")


def test_question_rejects_a_task_id_as_asked_by() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        make_question(clock=clock, asked_by=new_task_id(clock))


def test_question_rejects_too_many_options() -> None:
    with pytest.raises(ValidationError):
        make_question(options=tuple(f"opt{i}" for i in range(17)))


def test_question_is_frozen() -> None:
    question = make_question()

    with pytest.raises(ValidationError, match="frozen"):
        question.text = "changed"  # type: ignore[misc]  # The assignment is the test.


def test_question_rejects_an_unknown_field() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="extra"):
        Question.model_validate(
            {
                "id": new_message_id(clock),
                "task_id": new_task_id(clock),
                "asked_by": new_worker_id(clock),
                "text": "ok",
                "options": (),
                "clearance": "C1",
                "asked_at": clock.now().isoformat(),
                "status": "ASKED",
                "answer": None,
                "extra": "nope",
            }
        )


def test_question_json_round_trips() -> None:
    original = make_question(
        options=("a", "b"),
        status=QuestionStatus.ANSWERED,
        answer=make_answer(chosen_option=1),
    )

    restored = Question.model_validate_json(original.model_dump_json())

    assert restored == original
