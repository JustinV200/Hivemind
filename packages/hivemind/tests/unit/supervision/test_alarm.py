"""Tests for hivemind.supervision.alarm: AlarmKind, AlarmSeverity, Alarm, and AlarmState.

Fits into the Hive:
    Mirrors src/hivemind/supervision/alarm.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.alarm for the module under test.
    - .claude/codingrules.md section 14.3: "Every state machine has a test that walks every
      allowed transition and asserts every forbidden one raises."
"""

from __future__ import annotations

import itertools

import pytest
from builders.supervision import make_alarm
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.supervision.alarm import (
    TRANSITIONS,
    Alarm,
    AlarmKind,
    AlarmSeverity,
    AlarmState,
    assert_transition,
    can_transition,
)
from hivemind.supervision.errors import InvalidAlarmTransitionError
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_worker_id
from waggle.messages import AlarmSeverity as WireAlarmSeverity
from waggle.messages import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmRaised
from waggle.messages.supervision import AlarmKind as WireAlarmKind

# ──────────────────────────────────────────────────────────────────────────────
# AlarmKind / AlarmSeverity mirrors
# ──────────────────────────────────────────────────────────────────────────────


def test_alarm_kind_mirrors_the_wire_enum_member_for_member() -> None:
    hivemind_names = [member.name for member in AlarmKind]
    wire_names = [member.name for member in WireAlarmKind]

    assert hivemind_names == wire_names
    assert [m.value for m in AlarmKind] == [m.value for m in WireAlarmKind]


def test_alarm_severity_mirrors_the_wire_enum_member_for_member() -> None:
    hivemind_names = [member.name for member in AlarmSeverity]
    wire_names = [member.name for member in WireAlarmSeverity]

    assert hivemind_names == wire_names
    assert [m.value for m in AlarmSeverity] == [m.value for m in WireAlarmSeverity]


# ──────────────────────────────────────────────────────────────────────────────
# Alarm model
# ──────────────────────────────────────────────────────────────────────────────


def test_make_alarm_builds_a_valid_alarm() -> None:
    alarm = make_alarm()

    assert alarm.state is AlarmState.RAISED
    assert alarm.attempts == 0


def test_alarm_is_frozen() -> None:
    alarm = make_alarm()

    with pytest.raises(ValidationError, match="frozen"):
        alarm.attempts = 5  # type: ignore[misc]  # The assignment is the test.


def test_alarm_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError, match="extra"):
        Alarm.model_validate({**make_alarm().model_dump(), "extra": "nope"})


def test_alarm_rejects_negative_attempts() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        make_alarm(attempts=-1)


def test_alarm_from_wire_builds_the_equivalent_value_in_raised_state() -> None:
    clock = FakeClock()
    wire = AlarmRaised(
        alarm_id=new_alarm_id(clock),
        kind=WireAlarmKind.WORKER_CRASHED,
        severity=WireAlarmSeverity.CRITICAL,
        origin=new_worker_id(clock),
        attempts=2,
        raised_at=clock.now(),
        context=AlarmContext(
            task_id=None, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail="The worker process exited with code 1.",
        clearance=WireHoneyClearance.C1,
        reason="Cannot recover locally.",
    )

    alarm = Alarm.from_wire(wire)

    assert alarm.id == wire.alarm_id
    assert alarm.kind is AlarmKind.WORKER_CRASHED
    assert alarm.severity is AlarmSeverity.CRITICAL
    assert alarm.origin == wire.origin
    assert alarm.attempts == 2
    assert alarm.detail == wire.detail
    assert alarm.clearance is HoneyClearance.C1
    assert alarm.raised_at == wire.raised_at
    assert alarm.state is AlarmState.RAISED


def test_alarm_to_wire_round_trips_through_from_wire() -> None:
    original = make_alarm(kind=AlarmKind.PROVIDER_UNAVAILABLE, severity=AlarmSeverity.WARNING)

    wire = original.to_wire(reason="Provider is unreachable.")
    restored = Alarm.from_wire(wire)

    assert restored.id == original.id
    assert restored.kind == original.kind
    assert restored.severity == original.severity
    assert restored.origin == original.origin
    assert restored.attempts == original.attempts
    assert restored.detail == original.detail
    assert restored.clearance == original.clearance
    assert restored.raised_at == original.raised_at
    assert restored.state is AlarmState.RAISED


def test_alarm_to_wire_carries_the_given_reason() -> None:
    alarm = make_alarm()

    wire = alarm.to_wire(reason="Escalating after two failed retries.")

    assert wire.reason == "Escalating after two failed retries."


# ──────────────────────────────────────────────────────────────────────────────
# AlarmState / TRANSITIONS
# ──────────────────────────────────────────────────────────────────────────────

_ALLOWED_EDGES = [(state, target) for state, targets in TRANSITIONS.items() for target in targets]
_ALL_PAIRS = list(itertools.product(AlarmState, AlarmState))
_FORBIDDEN_EDGES = [pair for pair in _ALL_PAIRS if pair not in _ALLOWED_EDGES]


def test_transitions_has_exactly_one_entry_per_alarm_state() -> None:
    assert set(TRANSITIONS.keys()) == set(AlarmState)


def test_resolved_is_the_only_terminal_state() -> None:
    empty_edge_states = {state for state, targets in TRANSITIONS.items() if not targets}

    assert empty_edge_states == {AlarmState.RESOLVED}


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_can_transition_accepts_every_allowed_edge(
    from_state: AlarmState, to_state: AlarmState
) -> None:
    assert can_transition(from_state, to_state) is True


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_assert_transition_does_not_raise_on_every_allowed_edge(
    from_state: AlarmState, to_state: AlarmState
) -> None:
    assert_transition(from_state, to_state)


@pytest.mark.parametrize(("from_state", "to_state"), _FORBIDDEN_EDGES)
def test_can_transition_rejects_every_pair_outside_the_table(
    from_state: AlarmState, to_state: AlarmState
) -> None:
    assert can_transition(from_state, to_state) is False


@pytest.mark.parametrize(("from_state", "to_state"), _FORBIDDEN_EDGES)
def test_assert_transition_raises_on_every_pair_outside_the_table(
    from_state: AlarmState, to_state: AlarmState
) -> None:
    with pytest.raises(InvalidAlarmTransitionError):
        assert_transition(from_state, to_state)


def test_assert_transition_error_carries_the_alarm_id_when_given() -> None:
    with pytest.raises(InvalidAlarmTransitionError) as excinfo:
        assert_transition(AlarmState.RESOLVED, AlarmState.HANDLING, alarm_id="alarm_abc")

    assert excinfo.value.alarm_id == "alarm_abc"
    assert "alarm_abc" in str(excinfo.value)


def test_assert_transition_error_omits_the_alarm_id_when_not_given() -> None:
    with pytest.raises(InvalidAlarmTransitionError) as excinfo:
        assert_transition(AlarmState.RESOLVED, AlarmState.HANDLING)

    assert excinfo.value.alarm_id is None


def test_escalated_returns_to_handling_not_raised() -> None:
    # Codingrules section 8.8: the same alarm id climbs the tree; an escalation never resets to
    # RAISED, it starts HANDLING again at the next supervisor.
    assert can_transition(AlarmState.ESCALATED, AlarmState.HANDLING) is True
    assert can_transition(AlarmState.ESCALATED, AlarmState.RAISED) is False


def test_all_pairs_partition_into_allowed_and_forbidden_with_no_overlap() -> None:
    assert len(_ALLOWED_EDGES) + len(_FORBIDDEN_EDGES) == len(_ALL_PAIRS)
    assert set(_ALLOWED_EDGES).isdisjoint(_FORBIDDEN_EDGES)
