"""Tests for hivemind.memory.taint.marker: the one label, what it refuses, and where it may sit.

Fits into the Hive:
    Mirrors src/hivemind/memory/taint/marker.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.taint.marker for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.common.errors import InvariantViolationError
from hivemind.memory.taint import (
    TaintedKind,
    TaintMarker,
    TaintSource,
    TaintState,
    TaintTarget,
    is_refused,
    require_unlabelled,
)
from waggle.clock import FakeClock
from waggle.ids import new_event_id


def _marker(state: TaintState = TaintState.TAINTED, **overrides: object) -> TaintMarker:
    clock = FakeClock()
    fields: dict[str, object] = {
        "state": state,
        "source": TaintSource.ISOLATION,
        "reason": "Cell isolated on a Guard report.",
        "event_id": new_event_id(clock),
        "at": clock.now(),
    }
    fields.update(overrides)
    return TaintMarker(**fields)  # type: ignore[arg-type]


def test_only_a_tainted_marker_refuses_its_item() -> None:
    clock = FakeClock()
    cleared = _marker(
        TaintState.CLEARED, cleared_event_id=new_event_id(clock), cleared_at=clock.now()
    )

    assert is_refused(_marker()) and _marker().refuses
    assert not is_refused(cleared)
    assert not is_refused(None)


def test_a_cleared_marker_must_carry_its_clearing_and_a_tainted_one_must_not() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError, match="CLEARED"):
        _marker(TaintState.CLEARED)
    with pytest.raises(ValidationError, match="TAINTED"):
        _marker(cleared_event_id=new_event_id(clock), cleared_at=clock.now())


def test_a_marker_round_trips_through_json_and_refuses_extras() -> None:
    marker = _marker()

    assert TaintMarker.model_validate_json(marker.model_dump_json()) == marker
    with pytest.raises(ValidationError):
        TaintMarker.model_validate({**marker.model_dump(mode="json"), "text": "no"})


def test_a_target_names_an_item_by_a_hive_id() -> None:
    clock = FakeClock()

    target = TaintTarget(kind=TaintedKind.HANDOFF, item_id=new_event_id(clock))

    assert target.kind is TaintedKind.HANDOFF
    with pytest.raises(ValidationError, match="IdKind"):
        TaintTarget(kind=TaintedKind.EPISODE, item_id="not-an-id")


def test_a_new_item_must_arrive_unlabelled() -> None:
    require_unlabelled(None, "event_x")

    with pytest.raises(InvariantViolationError, match="arrived labelled"):
        require_unlabelled(_marker(), "event_x")
