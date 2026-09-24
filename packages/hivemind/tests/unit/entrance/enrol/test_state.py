"""Tests for hivemind.entrance.enrol.state: DeviceStatus and its one transition table.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.state for the module under test.
    - .claude/codingrules.md section 14.3: "Every state machine has a test that walks every
      allowed transition and asserts every forbidden one raises."
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.entrance.enrol.state import (
    APPROVED_TRAIL_KIND,
    ENTRY_TRAIL_KINDS,
    INVITED_TRAIL_KIND,
    TERMINAL_STATUSES,
    TRANSITIONS,
    DeviceStatus,
    assert_transition,
    can_transition,
    is_terminal,
    trail_kind,
)
from hivemind.entrance.errors import InvalidDeviceTransitionError
from hivemind.pheromone import GuardEvent

_S = DeviceStatus
# ADR-0033 and codingrules Appendix C, written out edge by edge: the table must be exactly this.
_DOCUMENTED_EDGES = {
    (_S.INVITED, _S.PENDING),
    (_S.INVITED, _S.EXPIRED),
    (_S.INVITED, _S.REVOKED),
    (_S.PENDING, _S.APPROVED),
    (_S.PENDING, _S.DENIED),
    (_S.PENDING, _S.EXPIRED),
    (_S.APPROVED, _S.LOCKED),
    (_S.APPROVED, _S.EXPIRED),
    (_S.APPROVED, _S.REVOKED),
    (_S.LOCKED, _S.APPROVED),
    (_S.LOCKED, _S.EXPIRED),
    (_S.LOCKED, _S.REVOKED),
}
_ALLOWED_EDGES = [(status, target) for status, targets in TRANSITIONS.items() for target in targets]
_FORBIDDEN_EDGES = [
    pair for pair in itertools.product(DeviceStatus, DeviceStatus) if pair not in _ALLOWED_EDGES
]


def test_the_table_has_one_entry_per_status_and_exactly_the_documented_edges() -> None:
    assert set(TRANSITIONS) == set(DeviceStatus)
    assert set(_ALLOWED_EDGES) == _DOCUMENTED_EDGES


def test_terminal_statuses_are_exactly_those_with_no_outgoing_edge() -> None:
    no_edges = {status for status, targets in TRANSITIONS.items() if not targets}

    assert no_edges == TERMINAL_STATUSES == {_S.DENIED, _S.EXPIRED, _S.REVOKED}
    assert all(is_terminal(status) for status in TERMINAL_STATUSES)
    assert not is_terminal(_S.LOCKED)


@pytest.mark.parametrize(("from_status", "to_status"), _ALLOWED_EDGES)
def test_every_allowed_edge_passes_and_names_its_trail_kind(
    from_status: DeviceStatus, to_status: DeviceStatus
) -> None:
    assert_transition(from_status, to_status)

    assert can_transition(from_status, to_status) is True
    expected = "unlocked" if to_status is _S.APPROVED and from_status is _S.LOCKED else None
    assert trail_kind(from_status, to_status) == (
        f"guard.entrance_{expected or to_status.name.lower()}"
    )


@pytest.mark.parametrize(("from_status", "to_status"), _FORBIDDEN_EDGES)
def test_every_forbidden_edge_raises(from_status: DeviceStatus, to_status: DeviceStatus) -> None:
    assert can_transition(from_status, to_status) is False
    with pytest.raises(InvalidDeviceTransitionError):
        assert_transition(from_status, to_status, "device_01M221E4C10R4XDPNQNRX85AAA")
    with pytest.raises(InvalidDeviceTransitionError):
        trail_kind(from_status, to_status)


def test_a_device_record_is_created_with_the_invited_kind() -> None:
    assert INVITED_TRAIL_KIND == "guard.entrance_invited"


def test_the_two_entries_are_an_invite_and_the_consoles_approval() -> None:
    assert dict(ENTRY_TRAIL_KINDS) == {
        _S.INVITED: "guard.entrance_invited",
        _S.APPROVED: "guard.entrance_approved",
    }
    assert trail_kind(_S.PENDING, _S.APPROVED) == APPROVED_TRAIL_KIND


def test_every_kind_the_machine_records_is_declared_by_the_guard_event_family() -> None:
    kinds = set(ENTRY_TRAIL_KINDS.values()) | {
        kind for targets in TRANSITIONS.values() for kind in targets.values()
    }

    assert kinds <= GuardEvent.KINDS


def test_the_error_for_a_forbidden_edge_carries_the_device_id() -> None:
    with pytest.raises(InvalidDeviceTransitionError) as excinfo:
        assert_transition(_S.REVOKED, _S.APPROVED, "device_01M221E4C10R4XDPNQNRX85AAA")

    assert excinfo.value.device_id == "device_01M221E4C10R4XDPNQNRX85AAA"
    assert excinfo.value.to_status is _S.APPROVED
