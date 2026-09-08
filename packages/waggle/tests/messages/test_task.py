"""Tests for the task family (waggle.messages.task and task_reports): all six classes.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    task class, which the registry step later loads by file path to seed its per-family checks;
    and for every class pins construction, the JSON round trip, the rejection of an extra field,
    at least one bound, every id field's kind, and every validator spec section 8.2 names, in
    both directions. The family's three enums and ArtifactRef are pinned here too.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the six task classes.

See Also:
    - waggle.messages.task and waggle.messages.task_reports for the modules under test.
    - docs/waggle/spec.md section 8.2 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from enum import Enum

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_PATH_CHARS, MAX_REASON_CHARS, MAX_SLOT_CHARS, WaggleMessage
from waggle.messages.labels import (
    AccuracyBar,
    HandoffRef,
    HoneyClearance,
    Postcondition,
    PostconditionKind,
    Tempo,
)
from waggle.messages.task import (
    MAX_ACCEPTANCE_CHARS,
    MAX_ACCEPTANCE_ITEMS,
    MAX_OBJECTIVE_CHARS,
    TaskAssign,
    TaskCancel,
    TaskPause,
    TaskResume,
    WorkerRole,
)
from waggle.messages.task_reports import (
    MAX_ARTIFACTS,
    MAX_SUMMARY_CHARS,
    ArtifactRef,
    TaskOutcome,
    TaskProgress,
    TaskResult,
    TaskStage,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
TASK_ID = new_id(IdKind.TASK, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
GRANT_ID = new_id(IdKind.GRANT, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
HANDOFF = HandoffRef(
    event_id=new_id(IdKind.EVENT, CLOCK), written_at=NOW, clearance=HoneyClearance.C1
)
RUBRIC = Postcondition(
    kind=PostconditionKind.JUDGE_RUBRIC,
    subject="review",
    argv=(),
    expected="The summary is faithful to the notes.",
)
TESTS_PASS = Postcondition(
    kind=PostconditionKind.TEST_PASSES, subject="tests/", argv=("pytest", "-q"), expected=None
)
ARTIFACT = ArtifactRef(path="scratch/summary.md", size_bytes=1_024, sha256="ab" * 32)
TASK_CLASSES: tuple[type[WaggleMessage], ...] = (
    TaskAssign,
    TaskProgress,
    TaskResult,
    TaskCancel,
    TaskPause,
    TaskResume,
)

# One valid instance of every task class, in catalogue order; the registry step loads this
# tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    TaskAssign(
        task_id=TASK_ID,
        goal_id=TASK_ID,
        cell_id=CELL_ID,
        role=WorkerRole.FORAGER,
        slot="FORAGER",
        objective="Summarise the release notes into one page.",
        acceptance=(RUBRIC, TESTS_PASS),
        tempo=Tempo(latency_budget_s=None, accuracy=AccuracyBar.NORMAL),
        clearance=HoneyClearance.C1,
        grant_id=GRANT_ID,
        attempt=1,
        resume_from=HANDOFF,
        reason="Placed on the Hive Stand: the notes are local to it.",
    ),
    TaskProgress(
        task_id=TASK_ID,
        attempt=1,
        stage=TaskStage.CHECKPOINTED,
        summary="Drafted the summary and checkpointed before the review.",
        clearance=HoneyClearance.C1,
        fraction_done=0.5,
        handoff=HANDOFF,
    ),
    TaskResult(
        task_id=TASK_ID,
        attempt=1,
        outcome=TaskOutcome.SUCCEEDED,
        summary="The summary was written and reviewed.",
        clearance=HoneyClearance.C1,
        artifacts=(ARTIFACT,),
        checked_by=WARDEN_ID,
        handoff=None,
        spend=0.42,
        reason="Both acceptance criteria passed.",
    ),
    TaskCancel(task_id=TASK_ID, grace_s=30.0, reason="The goal was withdrawn."),
    TaskPause(task_id=TASK_ID, reason="Clustering: the provider is down."),
    TaskResume(
        task_id=TASK_ID,
        attempt=2,
        resume_from=HANDOFF,
        slot="PLANNER",
        reason="The provider is back; a fresh bee resumes from the Handoff.",
    ),
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


def _criterion(
    subject_chars: int, argv: tuple[str, ...] = (), expected: str | None = None
) -> dict[str, object]:
    """A FILE_EXISTS (or, with an argv, COMMAND_EXITS_ZERO) criterion as a wire dict."""
    kind = PostconditionKind.COMMAND_EXITS_ZERO if argv else PostconditionKind.FILE_EXISTS
    return {"kind": kind.value, "subject": "s" * subject_chars, "argv": argv, "expected": expected}


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_task_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == TASK_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_task_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.task_id = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_task_message_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_task_id_rejects_an_id_of_another_kind(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="task_"):
        _rebuild(example, task_id=CELL_ID)


@pytest.mark.parametrize(
    "message_type", [TaskAssign, TaskResult, TaskCancel, TaskPause, TaskResume]
)
def test_reason_is_bounded_by_the_shared_limit(message_type: type[WaggleMessage]) -> None:
    example = _example(message_type)
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


@pytest.mark.parametrize("message_type", [TaskAssign, TaskProgress, TaskResult, TaskResume])
def test_attempt_starts_at_one(message_type: type[WaggleMessage]) -> None:
    example = _example(message_type)
    assert _rebuild(example, attempt=1).model_dump()["attempt"] == 1
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        _rebuild(example, attempt=0)


@pytest.mark.parametrize("message_type", [TaskAssign, TaskProgress, TaskResult])
def test_bounded_text_rejects_empty_and_over_long_values(
    message_type: type[WaggleMessage],
) -> None:
    field, limit = (
        ("objective", MAX_OBJECTIVE_CHARS)
        if message_type is TaskAssign
        else ("summary", MAX_SUMMARY_CHARS)
    )
    example = _example(message_type)
    assert _rebuild(example, **{field: "x" * limit})
    with pytest.raises(ValidationError, match="at least 1"):
        _rebuild(example, **{field: ""})
    with pytest.raises(ValidationError, match=f"at most {limit}"):
        _rebuild(example, **{field: "x" * (limit + 1)})


# ──────────────────────────────────────────────────────────────────────────────
# Enums and ArtifactRef
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("enum_type", "names"),
    [
        (WorkerRole, ["DRONE", "FORAGER", "SCOUT", "GUARD_BEE", "UNDERTAKER", "HOUSE_BEE"]),
        (TaskStage, ["STARTED", "WORKING", "CHECKPOINTED", "PAUSED", "RESUMED"]),
        (TaskOutcome, ["CLAIMED", "SUCCEEDED", "FAILED", "CANCELLED"]),
    ],
)
def test_enum_has_exactly_the_spec_members_with_values_equal_to_names(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert [member.value for member in enum_type] == names


def test_artifact_ref_round_trips_and_rejects_an_extra_field() -> None:
    assert ArtifactRef.model_validate(ARTIFACT.model_dump(mode="json")) == ARTIFACT
    with pytest.raises(ValidationError, match="extra"):
        ArtifactRef.model_validate({**ARTIFACT.model_dump(), "contents": "never"})


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"size_bytes": -1}, "greater than or equal to 0"),
        ({"sha256": "AB" * 32}, "pattern"),
        ({"sha256": "ab" * 31}, "pattern"),
        ({"path": "p" * (MAX_PATH_CHARS + 1)}, f"at most {MAX_PATH_CHARS}"),
    ],
)
def test_artifact_ref_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        ArtifactRef.model_validate({**ARTIFACT.model_dump(), **changes})


# ──────────────────────────────────────────────────────────────────────────────
# TaskAssign
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"goal_id": CELL_ID}, "task_"),
        ({"cell_id": TASK_ID}, "cell_"),
        ({"grant_id": TASK_ID}, "grant_"),
        ({"slot": "forager"}, "pattern"),
        ({"slot": "1FORAGER"}, "pattern"),
        ({"slot": ""}, "pattern"),
        ({"slot": "F" * (MAX_SLOT_CHARS + 1)}, f"at most {MAX_SLOT_CHARS}"),
        ({"acceptance": ()}, "at least 1"),
        ({"acceptance": (RUBRIC,) * (MAX_ACCEPTANCE_ITEMS + 1)}, f"at most {MAX_ACCEPTANCE_ITEMS}"),
        ({"clearance": "C0"}, "above the task's clearance"),
    ],
)
def test_task_assign_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(TaskAssign), **changes)


def test_task_assign_accepts_a_handoff_at_or_below_its_clearance_and_none() -> None:
    example = _example(TaskAssign)

    assert _rebuild(example, clearance="C1").model_dump()["clearance"] is HoneyClearance.C1
    assert _rebuild(example, clearance="C2").model_dump()["clearance"] is HoneyClearance.C2
    assert _rebuild(example, clearance="C0", resume_from=None).model_dump()["resume_from"] is None


def test_task_assign_bounds_the_total_characters_across_all_criteria() -> None:
    # Fifteen path-length subjects plus one command criterion land exactly on the bound; the
    # argv and expected text count too, so one more character in either tips it over.
    filler = [_criterion(MAX_PATH_CHARS) for _ in range(15)]
    last_subject = MAX_ACCEPTANCE_CHARS - 15 * MAX_PATH_CHARS - 2
    at_bound = [*filler, _criterion(last_subject, argv=("a",), expected="e")]
    over_by_argv = [*filler, _criterion(last_subject, argv=("ab",), expected="e")]
    over_by_expected = [*filler, _criterion(last_subject, argv=("a",), expected="ee")]

    assert _rebuild(_example(TaskAssign), acceptance=at_bound)
    for over in (over_by_argv, over_by_expected):
        with pytest.raises(ValidationError, match=f"more than the {MAX_ACCEPTANCE_CHARS}"):
            _rebuild(_example(TaskAssign), acceptance=over)


# ──────────────────────────────────────────────────────────────────────────────
# TaskProgress and TaskResult
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"handoff": None}, "required exactly when stage is CHECKPOINTED"),
        ({"stage": "WORKING"}, "required exactly when stage is CHECKPOINTED"),
        ({"fraction_done": -0.1}, "greater than or equal to 0"),
        ({"fraction_done": 1.1}, "less than or equal to 1"),
    ],
)
def test_task_progress_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(TaskProgress), **changes)


@pytest.mark.parametrize(
    "stage", [stage for stage in TaskStage if stage is not TaskStage.CHECKPOINTED]
)
def test_task_progress_accepts_any_other_stage_without_a_handoff(stage: TaskStage) -> None:
    progress = _rebuild(_example(TaskProgress), stage=stage.value, handoff=None, fraction_done=None)

    assert isinstance(progress, TaskProgress)
    assert progress.stage is stage
    assert progress.fraction_done is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"checked_by": None}, "set exactly when outcome is not CLAIMED"),
        ({"outcome": "CLAIMED"}, "set exactly when outcome is not CLAIMED"),
        ({"checked_by": TASK_ID}, "warden_"),
        ({"artifacts": (ARTIFACT.model_dump(),) * (MAX_ARTIFACTS + 1)}, f"at most {MAX_ARTIFACTS}"),
        ({"spend": -0.01}, "greater than or equal to 0"),
    ],
)
def test_task_result_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(TaskResult), **changes)


def test_task_result_claim_carries_no_checker_and_may_carry_a_handoff() -> None:
    claim = _rebuild(
        _example(TaskResult), outcome="CLAIMED", checked_by=None, artifacts=(), handoff=HANDOFF
    )

    assert isinstance(claim, TaskResult)
    assert claim.outcome is TaskOutcome.CLAIMED
    assert claim.checked_by is None
    assert claim.handoff == HANDOFF
    assert claim.model_dump(mode="json")["artifacts"] == []


@pytest.mark.parametrize("outcome", [TaskOutcome.FAILED, TaskOutcome.CANCELLED])
def test_task_result_every_terminal_outcome_names_its_checker(outcome: TaskOutcome) -> None:
    result = _rebuild(_example(TaskResult), outcome=outcome.value)

    assert isinstance(result, TaskResult)
    assert result.checked_by == WARDEN_ID


# ──────────────────────────────────────────────────────────────────────────────
# TaskCancel, TaskPause, TaskResume
# ──────────────────────────────────────────────────────────────────────────────


def test_task_cancel_grace_may_be_zero_but_never_negative() -> None:
    assert _rebuild(_example(TaskCancel), grace_s=0.0).model_dump()["grace_s"] == 0.0
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _rebuild(_example(TaskCancel), grace_s=-1.0)


def test_task_pause_carries_only_the_task_and_the_reason() -> None:
    assert list(TaskPause.model_fields) == ["task_id", "reason"]


def test_task_resume_in_place_keeps_the_binding_and_the_warm_bee() -> None:
    in_place = _rebuild(_example(TaskResume), resume_from=None, slot=None)

    assert isinstance(in_place, TaskResume)
    assert in_place.resume_from is None
    assert in_place.slot is None


@pytest.mark.parametrize("slot", ["planner", "1PLANNER", "", "P" * (MAX_SLOT_CHARS + 1)])
def test_task_resume_rejects_a_slot_outside_the_slot_shape(slot: str) -> None:
    with pytest.raises(ValidationError, match="slot"):
        _rebuild(_example(TaskResume), slot=slot)
