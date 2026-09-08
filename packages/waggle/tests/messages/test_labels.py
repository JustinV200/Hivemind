"""Tests for waggle.messages.labels: the shared enums, Tempo, Postcondition and HandoffRef.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins every enum's members and wire values to
    spec section 8.1, the rank order of the two totally ordered enums, and for each value model
    construction, the JSON round trip and at least one rejection, plus both Postcondition
    validators in both directions.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.labels for the module under test.
    - test_reports.py for the rest of spec section 8.1.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

import pytest
from pydantic import BaseModel, ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_PATH_CHARS
from waggle.messages.labels import (
    MAX_ARGV_ITEM_CHARS,
    MAX_ARGV_ITEMS,
    MAX_EXPECTED_CHARS,
    AccessLevel,
    AccuracyBar,
    AlarmSeverity,
    CombShieldLevel,
    HandoffRef,
    HoneyClearance,
    OsFamily,
    Postcondition,
    PostconditionKind,
    Tempo,
    Urgency,
)

CLOCK = FakeClock()

# Member names in declaration order, per spec section 8.1; every value equals its name.
_MEMBERS: list[tuple[type[Enum], list[str]]] = [
    (HoneyClearance, ["C0", "C1", "C2"]),
    (AccessLevel, ["READ_ONLY", "SCRATCH", "FULL"]),
    (CombShieldLevel, ["MEADOW", "PROPOLIS", "NIGHT_VEIL"]),
    (AlarmSeverity, ["INFO", "WARNING", "CRITICAL"]),
    (Urgency, ["GRACEFUL", "IMMEDIATE"]),
    (AccuracyBar, ["LOW", "NORMAL", "HIGH", "CRITICAL"]),
    (OsFamily, ["LINUX", "WINDOWS", "MACOS"]),
    (
        PostconditionKind,
        [
            "FILE_EXISTS",
            "FILE_ABSENT",
            "COMMAND_EXITS_ZERO",
            "TEST_PASSES",
            "HTTP_STATUS",
            "ELEMENT_TEXT",
            "JUDGE_RUBRIC",
        ],
    ),
]


def _round_trips(model: BaseModel) -> bool:
    """Whether ``model`` survives model_dump(mode="json") and model_validate unchanged."""
    return type(model).model_validate(model.model_dump(mode="json")) == model


def _postcondition(kind: PostconditionKind, **overrides: object) -> Postcondition:
    """A valid Postcondition of ``kind``, with the fields the kind needs, then ``overrides``."""
    fields: dict[str, object] = {"kind": kind, "subject": "subject", "argv": (), "expected": None}
    if kind in (PostconditionKind.COMMAND_EXITS_ZERO, PostconditionKind.TEST_PASSES):
        fields["argv"] = ("pytest", "-q")
    if kind in (
        PostconditionKind.HTTP_STATUS,
        PostconditionKind.ELEMENT_TEXT,
        PostconditionKind.JUDGE_RUBRIC,
    ):
        fields["expected"] = "200"
    return Postcondition.model_validate({**fields, **overrides})


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("enum_type", "names"), _MEMBERS)
def test_enum_has_exactly_the_spec_members_with_values_equal_to_names(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert [member.value for member in enum_type] == names


def test_honey_clearance_ranks_are_totally_ordered() -> None:
    ranks = [member.rank for member in HoneyClearance]

    assert ranks == [0, 1, 2]
    assert HoneyClearance.C0.rank < HoneyClearance.C1.rank < HoneyClearance.C2.rank


def test_access_level_ranks_are_totally_ordered() -> None:
    ranks = [member.rank for member in AccessLevel]

    assert ranks == [0, 1, 2]
    assert AccessLevel.READ_ONLY.rank < AccessLevel.SCRATCH.rank < AccessLevel.FULL.rank


# ──────────────────────────────────────────────────────────────────────────────
# Tempo
# ──────────────────────────────────────────────────────────────────────────────


def test_tempo_constructs_and_round_trips() -> None:
    budgeted = Tempo(latency_budget_s=2.5, accuracy=AccuracyBar.HIGH)
    unbudgeted = Tempo(latency_budget_s=None, accuracy=AccuracyBar.LOW)

    assert budgeted.model_dump(mode="json") == {"latency_budget_s": 2.5, "accuracy": "HIGH"}
    assert _round_trips(budgeted)
    assert _round_trips(unbudgeted)


@pytest.mark.parametrize("budget", [0.0, -1.0])
def test_tempo_rejects_a_budget_that_is_not_positive(budget: float) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        Tempo(latency_budget_s=budget, accuracy=AccuracyBar.NORMAL)


def test_tempo_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Tempo.model_validate({"latency_budget_s": None, "accuracy": "NORMAL", "speed": 1})


# ──────────────────────────────────────────────────────────────────────────────
# Postcondition
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", list(PostconditionKind))
def test_postcondition_constructs_and_round_trips_for_every_kind(kind: PostconditionKind) -> None:
    postcondition = _postcondition(kind)

    assert postcondition.kind is kind
    assert _round_trips(postcondition)


def test_postcondition_argv_is_allowed_only_on_command_kinds() -> None:
    command = _postcondition(PostconditionKind.COMMAND_EXITS_ZERO, argv=("make", "test"))
    assert command.argv == ("make", "test")
    # The spec says "non-empty only for" the two command kinds, not "non-empty for": an empty
    # argv on a command kind is accepted, an argv on any other kind is not.
    assert _postcondition(PostconditionKind.TEST_PASSES, argv=()).argv == ()
    with pytest.raises(ValidationError, match="carries no argv"):
        _postcondition(PostconditionKind.FILE_EXISTS, argv=("ls",))


def test_postcondition_expected_is_required_on_comparison_kinds() -> None:
    assert _postcondition(PostconditionKind.ELEMENT_TEXT, expected="Saved").expected == "Saved"
    assert _postcondition(PostconditionKind.FILE_ABSENT, expected="anything").expected == "anything"
    for kind in (
        PostconditionKind.HTTP_STATUS,
        PostconditionKind.ELEMENT_TEXT,
        PostconditionKind.JUDGE_RUBRIC,
    ):
        with pytest.raises(ValidationError, match="requires `expected`"):
            _postcondition(kind, expected=None)


def test_postcondition_bounds() -> None:
    with pytest.raises(ValidationError, match="at least 1"):
        _postcondition(PostconditionKind.FILE_EXISTS, subject="")
    with pytest.raises(ValidationError, match=f"at most {MAX_PATH_CHARS}"):
        _postcondition(PostconditionKind.FILE_EXISTS, subject="x" * (MAX_PATH_CHARS + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_ARGV_ITEMS}"):
        _postcondition(PostconditionKind.TEST_PASSES, argv=("x",) * (MAX_ARGV_ITEMS + 1))
    with pytest.raises(ValidationError, match=f"at most {MAX_ARGV_ITEM_CHARS}"):
        _postcondition(PostconditionKind.TEST_PASSES, argv=("x" * (MAX_ARGV_ITEM_CHARS + 1),))
    with pytest.raises(ValidationError, match=f"at most {MAX_EXPECTED_CHARS}"):
        _postcondition(PostconditionKind.JUDGE_RUBRIC, expected="x" * (MAX_EXPECTED_CHARS + 1))


def test_postcondition_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _postcondition(PostconditionKind.FILE_EXISTS, timeout_s=5)


# ──────────────────────────────────────────────────────────────────────────────
# HandoffRef
# ──────────────────────────────────────────────────────────────────────────────


def test_handoff_ref_constructs_and_round_trips() -> None:
    ref = HandoffRef(
        event_id=new_id(IdKind.EVENT, CLOCK),
        written_at=datetime(2020, 1, 1, tzinfo=UTC),
        clearance=HoneyClearance.C1,
    )

    assert ref.model_dump(mode="json")["clearance"] == "C1"
    assert _round_trips(ref)


def test_handoff_ref_rejects_a_non_event_id_and_a_naive_time() -> None:
    with pytest.raises(ValidationError, match="event_"):
        HandoffRef(
            event_id=new_id(IdKind.TASK, CLOCK),
            written_at=datetime(2020, 1, 1, tzinfo=UTC),
            clearance=HoneyClearance.C0,
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        HandoffRef(
            event_id=new_id(IdKind.EVENT, CLOCK),
            written_at=datetime(2020, 1, 1),  # naive on purpose
            clearance=HoneyClearance.C0,
        )
