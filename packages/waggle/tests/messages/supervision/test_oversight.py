"""Tests for the supervision family's messages: all eight classes, plus the four in supervision.py.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Holds EXAMPLES, one valid instance of every
    supervision class across its four modules, which the registry step later loads by file path
    to seed its per-family checks; and for every class pins construction, the JSON round trip,
    the rejection of an extra field and the shared reason bound. The per-class bounds and
    validators of Heartbeat, Inspect, InspectReply and Intervene are pinned here; those of the
    other four messages and of the value models live in the sibling test modules.

Key invariants:
    - EXAMPLES holds exactly one instance of each of the eight supervision classes.

See Also:
    - waggle.messages.supervision.oversight for the module under test.
    - test_telemetry.py, test_alarms.py and
      test_questions.py for the rest of the family.
    - docs/waggle/spec.md section 8.3 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

from enum import Enum

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.base import MAX_REASON_CHARS, MAX_SUB_BEES_ON_WIRE, WaggleMessage
from waggle.messages.labels import AlarmSeverity, HoneyClearance
from waggle.messages.supervision.alarms import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    AlarmResolution,
    AlarmResolved,
)
from waggle.messages.supervision.oversight import (
    MAX_BINDING_CHARS,
    MIN_INSPECT_CHARS,
    Heartbeat,
    Inspect,
    InspectReply,
    Intervene,
    InterventionAction,
)
from waggle.messages.supervision.questions import Answer, AnswerSource, Question
from waggle.messages.supervision.telemetry import (
    MAX_VIEW_CHARS,
    ChildTelemetry,
    CompactView,
    ContextTelemetry,
    WardenState,
    WorkerState,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
TASK_ID = new_id(IdKind.TASK, CLOCK)
WORKER_ID = new_id(IdKind.WORKER, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
HIVE_ID = new_id(IdKind.HIVE, CLOCK)
GRANT_ID = new_id(IdKind.GRANT, CLOCK)
ALARM_ID = new_id(IdKind.ALARM, CLOCK)
QUESTION_ID = new_id(IdKind.MESSAGE, CLOCK)
TELEMETRY = ContextTelemetry(
    tokens_used=12_000,
    context_window=200_000,
    goal="Summarise the release notes.",
    last_actions=("Read notes.md", "Drafted summary.md"),
    blockers=(),
    spend=0.12,
)
CHILD = ChildTelemetry(
    worker_id=WORKER_ID, task_id=TASK_ID, state=WorkerState.RUNNING, telemetry=TELEMETRY
)
VIEW = CompactView(
    goal="Summarise the release notes.",
    progress="The draft is written; the review is next.",
    decisions=("Kept the changelog order.",),
    open_threads=("Confirm the version string.",),
)
EMPTY_CONTEXT = AlarmContext(
    task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None
)
SUPERVISION_CLASSES: tuple[type[WaggleMessage], ...] = (
    Heartbeat,
    AlarmRaised,
    AlarmResolved,
    Inspect,
    InspectReply,
    Intervene,
    Question,
    Answer,
)

# One valid instance of every supervision class, in catalogue order; the registry step loads
# this tuple by file path, so its name and shape are part of the contract.
EXAMPLES: tuple[WaggleMessage, ...] = (
    Heartbeat(
        telemetry=TELEMETRY,
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(CHILD,),
        grant_id=GRANT_ID,
        grant_spend=0.12,
        interval_s=15.0,
    ),
    AlarmRaised(
        alarm_id=ALARM_ID,
        kind=AlarmKind.WORKER_STALLED,
        severity=AlarmSeverity.WARNING,
        origin=WARDEN_ID,
        attempts=0,
        raised_at=NOW,
        context=AlarmContext(
            task_id=TASK_ID, cell_id=None, worker_id=WORKER_ID, event_id=None, handoff=None
        ),
        detail="No heartbeat from the Worker for three intervals.",
        clearance=HoneyClearance.C1,
        reason="A respawn needs a fresh grant slice the Warden cannot carve.",
    ),
    AlarmResolved(
        alarm_id=ALARM_ID,
        resolution=AlarmResolution.RESPAWNED,
        resolved_by=HIVE_ID,
        reason="The Queen issued a fresh slice and the Warden respawned the Worker.",
    ),
    Inspect(subject=WORKER_ID, max_chars=2_000),
    InspectReply(
        subject=WORKER_ID,
        task_id=TASK_ID,
        telemetry=TELEMETRY,
        view=VIEW,
        clearance=HoneyClearance.C1,
        is_truncated=False,
    ),
    Intervene(
        action=InterventionAction.REBIND,
        subject=WORKER_ID,
        task_id=TASK_ID,
        slot="PLANNER",
        alarm_id=ALARM_ID,
        reason="The current slot's provider keeps timing out.",
    ),
    Question(
        question_id=QUESTION_ID,
        task_id=TASK_ID,
        asked_by=WORKER_ID,
        text="Which version string should the summary quote?",
        options=("1.4.0", "1.4.1"),
        clearance=HoneyClearance.C1,
        asked_at=NOW,
    ),
    Answer(
        question_id=QUESTION_ID,
        task_id=TASK_ID,
        text="Quote 1.4.1; it is the tagged release.",
        chosen_option=1,
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
    ),
)


def _rebuild(example: WaggleMessage, **changes: object) -> WaggleMessage:
    """Re-validate ``example`` with some fields replaced."""
    return type(example).model_validate({**example.model_dump(), **changes})


def _example(message_type: type[WaggleMessage]) -> WaggleMessage:
    """The EXAMPLES entry of ``message_type``."""
    return next(example for example in EXAMPLES if type(example) is message_type)


# ──────────────────────────────────────────────────────────────────────────────
# Every class
# ──────────────────────────────────────────────────────────────────────────────


def test_examples_hold_exactly_one_instance_of_every_supervision_class() -> None:
    assert tuple(type(example) for example in EXAMPLES) == SUPERVISION_CLASSES


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_supervision_message_round_trips_and_is_frozen(example: WaggleMessage) -> None:
    assert type(example).model_validate(example.model_dump(mode="json")) == example
    with pytest.raises(ValidationError, match="frozen"):
        example.task_id = "changed"  # type: ignore[attr-defined]  # The assignment is the test.


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda example: type(example).__name__)
def test_supervision_message_rejects_an_extra_field(example: WaggleMessage) -> None:
    with pytest.raises(ValidationError, match="extra"):
        _rebuild(example, hop_count=1)


@pytest.mark.parametrize("message_type", [AlarmRaised, AlarmResolved, Intervene])
def test_reason_is_bounded_by_the_shared_limit(message_type: type[WaggleMessage]) -> None:
    example = _example(message_type)
    assert _rebuild(example, reason="x" * MAX_REASON_CHARS)
    with pytest.raises(ValidationError, match=f"at most {MAX_REASON_CHARS}"):
        _rebuild(example, reason="x" * (MAX_REASON_CHARS + 1))


def test_intervention_action_has_exactly_the_spec_members_with_values_equal_to_names() -> None:
    names = ["COMPACT", "CHECKPOINT", "HANDOFF", "REBIND", "TAKEOVER", "CANCEL"]
    enum_type: type[Enum] = InterventionAction

    assert [member.name for member in enum_type] == names
    assert [member.value for member in enum_type] == names


# ──────────────────────────────────────────────────────────────────────────────
# Heartbeat
# ──────────────────────────────────────────────────────────────────────────────


def test_heartbeat_from_a_worker_carries_its_own_state_and_no_rows() -> None:
    beat = _rebuild(
        _example(Heartbeat),
        task_id=TASK_ID,
        worker_state="RUNNING",
        warden_state=None,
        children=(),
        grant_id=None,
        grant_spend=None,
    )

    assert isinstance(beat, Heartbeat)
    assert beat.worker_state is WorkerState.RUNNING
    assert beat.warden_state is None
    assert beat.model_dump(mode="json")["children"] == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"warden_state": None}, "exactly one of worker_state and warden_state"),
        ({"worker_state": "RUNNING"}, "exactly one of worker_state and warden_state"),
        ({"children": (CHILD.model_dump(), CHILD.model_dump())}, "each worker id once"),
        (
            {
                "children": tuple(
                    {**CHILD.model_dump(), "worker_id": new_id(IdKind.WORKER, CLOCK)}
                    for _ in range(MAX_SUB_BEES_ON_WIRE + 1)
                )
            },
            f"at most {MAX_SUB_BEES_ON_WIRE}",
        ),
        ({"grant_id": None}, "requires a grant_id"),
        ({"grant_spend": -0.01}, "greater than or equal to 0"),
        ({"grant_id": TASK_ID}, "grant_"),
        ({"task_id": WORKER_ID}, "task_"),
        ({"interval_s": 0.0}, "greater than 0"),
    ],
)
def test_heartbeat_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(Heartbeat), **changes)


def test_heartbeat_accepts_a_grant_without_a_spend_figure_and_the_full_row_cap() -> None:
    rows = tuple(
        {**CHILD.model_dump(), "worker_id": new_id(IdKind.WORKER, CLOCK)}
        for _ in range(MAX_SUB_BEES_ON_WIRE)
    )
    beat = _rebuild(_example(Heartbeat), grant_spend=None, children=rows)

    assert isinstance(beat, Heartbeat)
    assert beat.grant_spend is None
    assert len(beat.children) == MAX_SUB_BEES_ON_WIRE


# ──────────────────────────────────────────────────────────────────────────────
# Inspect and InspectReply
# ──────────────────────────────────────────────────────────────────────────────


def test_inspect_defaults_to_the_largest_view_and_may_target_the_recipient_itself() -> None:
    inspect = Inspect(subject=None)

    assert inspect.max_chars == MAX_VIEW_CHARS
    assert inspect.subject is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"max_chars": MIN_INSPECT_CHARS - 1}, f"greater than or equal to {MIN_INSPECT_CHARS}"),
        ({"max_chars": MAX_VIEW_CHARS + 1}, f"less than or equal to {MAX_VIEW_CHARS}"),
        ({"subject": WARDEN_ID}, "worker_"),
    ],
)
def test_inspect_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(Inspect), **changes)


def test_inspect_reply_subject_is_a_worker_or_a_warden_never_anything_else() -> None:
    example = _example(InspectReply)

    assert _rebuild(example, subject=WARDEN_ID).model_dump()["subject"] == WARDEN_ID
    for other in (HIVE_ID, TASK_ID, "worker_not_a_ulid"):
        with pytest.raises(ValidationError, match="subject"):
            _rebuild(example, subject=other)


def test_inspect_reply_may_be_truncated_and_idle() -> None:
    reply = _rebuild(_example(InspectReply), task_id=None, is_truncated=True)

    assert isinstance(reply, InspectReply)
    assert reply.task_id is None
    assert reply.is_truncated is True


# ──────────────────────────────────────────────────────────────────────────────
# Intervene
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "action", [action for action in InterventionAction if action is not InterventionAction.REBIND]
)
def test_intervene_every_other_lever_carries_no_slot(action: InterventionAction) -> None:
    intervene = _rebuild(
        _example(Intervene),
        action=action.value,
        slot=None,
        subject=None,
        task_id=None,
        alarm_id=None,
    )

    assert isinstance(intervene, Intervene)
    assert intervene.action is action
    assert intervene.slot is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"slot": None}, "required exactly when action is REBIND"),
        ({"action": "CANCEL"}, "required exactly when action is REBIND"),
        ({"slot": "planner"}, "pattern"),
        ({"subject": WARDEN_ID}, "worker_"),
        ({"alarm_id": TASK_ID}, "alarm_"),
    ],
)
def test_intervene_validators(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        _rebuild(_example(Intervene), **changes)


def test_intervene_binding_defaults_to_none_and_is_independent_of_action() -> None:
    """PROTOCOL_MINOR 2: an optional REBIND hint, unset unless a caller names one."""
    intervene = _example(Intervene)

    assert isinstance(intervene, Intervene)
    assert intervene.binding is None


def test_intervene_binding_round_trips_when_set() -> None:
    """A sender that already resolved a fallback key (the Queen's own lookup) can name it."""
    intervene = _rebuild(_example(Intervene), binding="local_worker")

    assert isinstance(intervene, Intervene)
    assert intervene.binding == "local_worker"
    assert Intervene.model_validate(intervene.model_dump(mode="json")) == intervene


def test_intervene_binding_is_bounded() -> None:
    with pytest.raises(ValidationError, match="at most"):
        _rebuild(_example(Intervene), binding="x" * (MAX_BINDING_CHARS + 1))
