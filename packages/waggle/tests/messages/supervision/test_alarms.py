"""Tests for waggle.messages.supervision.alarms: AlarmRaised, AlarmResolved and their value types.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins AlarmKind's and AlarmResolution's members
    and wire values to spec section 8.3; for AlarmContext construction, the JSON round trip,
    the rejection of an extra field and every id's kind; and for the two messages every bound
    and id rule the spec states. The family-wide round trip, extra-field and reason checks run
    in test_oversight.py over its EXAMPLES.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.supervision.alarms for the module under test.
    - test_oversight.py for EXAMPLES and the checks every supervision class shares.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

import pytest
from pydantic import ValidationError

from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id
from waggle.messages.labels import AlarmSeverity, HandoffRef, HoneyClearance
from waggle.messages.supervision.alarms import (
    MAX_DETAIL_CHARS,
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    AlarmResolution,
    AlarmResolved,
)

CLOCK = FakeClock()
NOW = CLOCK.now()
TASK_ID = new_id(IdKind.TASK, CLOCK)
CELL_ID = new_id(IdKind.CELL, CLOCK)
WORKER_ID = new_id(IdKind.WORKER, CLOCK)
WARDEN_ID = new_id(IdKind.WARDEN, CLOCK)
HIVE_ID = new_id(IdKind.HIVE, CLOCK)
EVENT_ID = new_id(IdKind.EVENT, CLOCK)
ALARM_ID = new_id(IdKind.ALARM, CLOCK)
HANDOFF = HandoffRef(event_id=EVENT_ID, written_at=NOW, clearance=HoneyClearance.C1)
CONTEXT = AlarmContext(
    task_id=TASK_ID, cell_id=CELL_ID, worker_id=WORKER_ID, event_id=EVENT_ID, handoff=HANDOFF
)
RAISED = AlarmRaised(
    alarm_id=ALARM_ID,
    kind=AlarmKind.WORKER_STALLED,
    severity=AlarmSeverity.WARNING,
    origin=WORKER_ID,
    attempts=1,
    raised_at=NOW,
    context=CONTEXT,
    detail="No heartbeat from the Worker for three intervals.",
    clearance=HoneyClearance.C1,
    reason="A respawn needs a fresh grant slice the Warden cannot carve.",
)
RESOLVED = AlarmResolved(
    alarm_id=ALARM_ID,
    resolution=AlarmResolution.RESPAWNED,
    resolved_by=WARDEN_ID,
    reason="The Warden respawned the Worker from its Handoff.",
)


# ──────────────────────────────────────────────────────────────────────────────
# Enums and AlarmContext
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("enum_type", "names"),
    [
        (
            AlarmKind,
            [
                "WORKER_FAILED",
                "WORKER_CRASHED",
                "WORKER_STALLED",
                "ACCEPTANCE_FAILED",
                "POSTCONDITION_FAILED",
                "CONTEXT_OVERFLOW",
                "GRANT_EXCEEDED",
                "PROVIDER_UNAVAILABLE",
                "AUDIT_FAILED",
                "CELL_UNREACHABLE",
                "QUOTA_EXCEEDED",
                "OTHER",
            ],
        ),
        (
            AlarmResolution,
            [
                "RETRIED",
                "RESPAWNED",
                "REBOUND",
                "TAKEN_OVER",
                "CANCELLED",
                "HUMAN",
                "SELF_CLEARED",
            ],
        ),
    ],
)
def test_enum_has_exactly_the_spec_members_with_values_equal_to_names(
    enum_type: type[Enum], names: list[str]
) -> None:
    assert [member.name for member in enum_type] == names
    assert [member.value for member in enum_type] == names


def test_alarm_context_round_trips_may_be_empty_and_rejects_an_extra_field() -> None:
    empty = AlarmContext(task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None)

    assert AlarmContext.model_validate(CONTEXT.model_dump(mode="json")) == CONTEXT
    assert all(value is None for value in empty.model_dump().values())
    with pytest.raises(ValidationError, match="extra"):
        AlarmContext.model_validate({**CONTEXT.model_dump(), "transcript": "never"})


@pytest.mark.parametrize(
    ("field", "wrong_id", "prefix"),
    [
        ("task_id", CELL_ID, "task_"),
        ("cell_id", TASK_ID, "cell_"),
        ("worker_id", WARDEN_ID, "worker_"),
        ("event_id", ALARM_ID, "event_"),
    ],
)
def test_alarm_context_rejects_an_id_of_another_kind(
    field: str, wrong_id: str, prefix: str
) -> None:
    with pytest.raises(ValidationError, match=prefix):
        AlarmContext.model_validate({**CONTEXT.model_dump(), field: wrong_id})


# ──────────────────────────────────────────────────────────────────────────────
# AlarmRaised
# ──────────────────────────────────────────────────────────────────────────────


def test_alarm_raised_origin_is_a_worker_or_a_warden_never_anything_else() -> None:
    wire = RAISED.model_dump()

    assert AlarmRaised.model_validate({**wire, "origin": WARDEN_ID}).origin == WARDEN_ID
    for other in (HIVE_ID, TASK_ID, "worker_not_a_ulid"):
        with pytest.raises(ValidationError, match="origin"):
            AlarmRaised.model_validate({**wire, "origin": other})


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"alarm_id": TASK_ID}, "alarm_"),
        ({"attempts": -1}, "greater than or equal to 0"),
        ({"raised_at": datetime(2020, 1, 1)}, "timezone-aware"),  # naive on purpose
        ({"detail": ""}, "at least 1"),
        ({"detail": "d" * (MAX_DETAIL_CHARS + 1)}, f"at most {MAX_DETAIL_CHARS}"),
        ({"kind": "WORKER_BORED"}, "kind"),
        ({"severity": "FATAL"}, "severity"),
        ({"clearance": "C3"}, "clearance"),
    ],
)
def test_alarm_raised_bounds(changes: dict[str, object], reason: str) -> None:
    with pytest.raises(ValidationError, match=reason):
        AlarmRaised.model_validate({**RAISED.model_dump(), **changes})


def test_alarm_raised_on_the_raising_hop_has_tried_nothing_yet() -> None:
    first_hop = AlarmRaised.model_validate({**RAISED.model_dump(), "attempts": 0})

    assert first_hop.attempts == 0
    assert first_hop.raised_at == NOW


# ──────────────────────────────────────────────────────────────────────────────
# AlarmResolved
# ──────────────────────────────────────────────────────────────────────────────


def test_alarm_resolved_by_a_warden_or_the_queen_never_a_worker() -> None:
    wire = RESOLVED.model_dump()

    assert AlarmResolved.model_validate({**wire, "resolved_by": HIVE_ID}).resolved_by == HIVE_ID
    for other in (WORKER_ID, TASK_ID, "hive_not_a_ulid"):
        with pytest.raises(ValidationError, match="resolved_by"):
            AlarmResolved.model_validate({**wire, "resolved_by": other})


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"alarm_id": WORKER_ID}, "alarm_"),
        ({"resolution": "IGNORED"}, "resolution"),
    ],
)
def test_alarm_resolved_rejects_a_foreign_id_and_an_unknown_resolution(
    changes: dict[str, object], reason: str
) -> None:
    with pytest.raises(ValidationError, match=reason):
        AlarmResolved.model_validate({**RESOLVED.model_dump(), **changes})
