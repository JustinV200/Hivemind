"""Tests for waggle.messages.supervision.questions: Question, Answer and AnswerSource.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins AnswerSource's members and wire values to
    spec section 8.3, every bound and id rule of Question and Answer, and Answer's
    human-answer-is-C2 validator in both directions. The family-wide round trip, extra-field
    and reason checks run in test_oversight.py over its EXAMPLES.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.supervision.questions for the module under test.
    - test_oversight.py for EXAMPLES and the checks every supervision class shares.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.labels import HoneyClearance
from waggle.messages.supervision.questions import (
    MAX_OPTION_CHARS,
    MAX_OPTION_INDEX,
    MAX_OPTIONS,
    MAX_TEXT_CHARS,
    Answer,
    AnswerSource,
    Question,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
TASK_ID = new_id(IdKind.TASK, CLOCK)
WORKER_ID = new_id(IdKind.WORKER, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
HIVE_ID = new_id(IdKind.HIVE, CLOCK)
QUESTION_ID = new_id(IdKind.MESSAGE, CLOCK)
QUESTION = Question(
    question_id=QUESTION_ID,
    task_id=TASK_ID,
    asked_by=WORKER_ID,
    text="Which version string should the summary quote?",
    options=("1.4.0", "1.4.1"),
    clearance=HoneyClearance.C1,
    asked_at=NOW,
)
ANSWER = Answer(
    question_id=QUESTION_ID,
    task_id=TASK_ID,
    text="Quote 1.4.1; it is the tagged release.",
    chosen_option=1,
    source=AnswerSource.QUEEN,
    clearance=HoneyClearance.C1,
)


def test_answer_source_has_exactly_the_spec_members_with_values_equal_to_names() -> None:
    assert [member.name for member in AnswerSource] == ["HUMAN", "QUEEN", "WARDEN"]
    assert [member.value for member in AnswerSource] == ["HUMAN", "QUEEN", "WARDEN"]


def test_option_index_bound_is_the_last_index_of_a_full_options_tuple() -> None:
    assert MAX_OPTION_INDEX == MAX_OPTIONS - 1 == 15


# ──────────────────────────────────────────────────────────────────────────────
# Question
# ──────────────────────────────────────────────────────────────────────────────


def test_question_asked_by_a_worker_or_a_warden_never_anything_else() -> None:
    wire = QUESTION.model_dump()

    assert Question.model_validate({**wire, "asked_by": WARDEN_ID}).asked_by == WARDEN_ID
    for other in (HIVE_ID, TASK_ID, "warden_not_a_ulid"):
        with pytest.raises(ValidationError, match="asked_by"):
            Question.model_validate({**wire, "asked_by": other})


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"question_id": TASK_ID}, "msg_"),
        ({"task_id": QUESTION_ID}, "task_"),
        ({"text": ""}, "at least 1"),
        ({"text": "q" * (MAX_TEXT_CHARS + 1)}, f"at most {MAX_TEXT_CHARS}"),
        ({"options": ("o",) * (MAX_OPTIONS + 1)}, f"at most {MAX_OPTIONS}"),
        ({"options": ("o" * (MAX_OPTION_CHARS + 1),)}, f"at most {MAX_OPTION_CHARS}"),
        ({"asked_at": datetime(2020, 1, 1)}, "timezone-aware"),  # naive on purpose
    ],
)
def test_question_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        Question.model_validate({**QUESTION.model_dump(), **changes})


def test_question_may_offer_no_options_or_the_full_sixteen() -> None:
    wire = QUESTION.model_dump()
    open_question = Question.model_validate({**wire, "options": ()})
    closed = Question.model_validate({**wire, "options": tuple(str(i) for i in range(MAX_OPTIONS))})

    assert open_question.model_dump(mode="json")["options"] == []
    assert len(closed.options) == MAX_OPTIONS


# ──────────────────────────────────────────────────────────────────────────────
# Answer
# ──────────────────────────────────────────────────────────────────────────────


def test_answer_from_the_human_is_c2_by_provenance() -> None:
    wire = ANSWER.model_dump()
    human = Answer.model_validate({**wire, "source": "HUMAN", "clearance": "C2"})

    assert human.source is AnswerSource.HUMAN
    assert human.clearance is HoneyClearance.C2
    for lower in ("C0", "C1"):
        with pytest.raises(ValidationError, match="HUMAN requires clearance C2"):
            Answer.model_validate({**wire, "source": "HUMAN", "clearance": lower})


@pytest.mark.parametrize("source", [AnswerSource.QUEEN, AnswerSource.WARDEN])
@pytest.mark.parametrize("clearance", list(HoneyClearance))
def test_answer_from_a_bee_may_carry_any_clearance(
    source: AnswerSource, clearance: HoneyClearance
) -> None:
    answer = Answer.model_validate(
        {**ANSWER.model_dump(), "source": source.value, "clearance": clearance.value}
    )

    assert answer.source is source
    assert answer.clearance is clearance


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"question_id": WORKER_ID}, "msg_"),
        ({"task_id": QUESTION_ID}, "task_"),
        ({"text": ""}, "at least 1"),
        ({"text": "a" * (MAX_TEXT_CHARS + 1)}, f"at most {MAX_TEXT_CHARS}"),
        ({"chosen_option": -1}, "greater than or equal to 0"),
        ({"chosen_option": MAX_OPTION_INDEX + 1}, f"less than or equal to {MAX_OPTION_INDEX}"),
        ({"source": "DEVICE"}, "source"),
    ],
)
def test_answer_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        Answer.model_validate({**ANSWER.model_dump(), **changes})


def test_answer_in_prose_chooses_no_option() -> None:
    prose = Answer.model_validate({**ANSWER.model_dump(), "chosen_option": None})

    assert prose.chosen_option is None
    assert Answer.model_validate({**ANSWER.model_dump(), "chosen_option": MAX_OPTION_INDEX})
