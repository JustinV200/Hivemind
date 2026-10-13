"""Test the device lease record and its state machine: OPEN, then RELEASING, then RELEASED.

A device lease is the packet-side record of the Hive's temporary hold on a borrowed device
(gateway-contract section 12 and codingrules Appendix C): OPEN while session traffic is accepted,
RELEASING while a release runs or is retried after a failed step, RELEASED once everything has been
undone, at which point it leaves the table. Only the edges in the transition table are legal, and
each carries the reason it exists. The record also tracks the programs the lease started and the
paths it changed outside scratch, and it is a boundary model: it round-trips through JSON and
refuses what its fields refuse.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.lease.lease.

Key invariants:
    - Every legal edge is in ``TRANSITIONS`` and nothing else is.
    - Updating a lease returns a new lease; the old value is never edited.

See Also:
    - docs/waggle/gateway-contract.md section 12.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from states import sample_state

from pollen.errors import InvalidTransitionError
from pollen.lease import (
    TRANSITIONS,
    DeviceLease,
    LeaseState,
    ProcessKind,
    ProcessRecord,
    RestoreKind,
    RestoreRecord,
    transition,
)
from waggle.clock import FakeClock
from waggle.ids import new_lease_id
from waggle.messages.cell import ReleaseCause
from waggle.messages.labels import AccessLevel


def _lease(clock: FakeClock) -> DeviceLease:
    """Build an open lease for a fresh device."""
    state = sample_state(clock)
    return DeviceLease(
        lease_id=new_lease_id(clock),
        cell_id=state.cell_id,
        holder=state.warden_id,
        access_level=AccessLevel.SCRATCH,
        scratch_root="/data/leases/one",
        quota_bytes=1_000,
        opened_at=clock.now(),
    )


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (LeaseState.OPEN, LeaseState.RELEASING),
        (LeaseState.RELEASING, LeaseState.RELEASING),
        (LeaseState.RELEASING, LeaseState.RELEASED),
    ],
)
def test_the_legal_edges_are_taken(current: LeaseState, new: LeaseState) -> None:
    assert transition(current, new) is new
    assert new in TRANSITIONS[current]


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (LeaseState.OPEN, LeaseState.OPEN),
        (LeaseState.OPEN, LeaseState.RELEASED),
        (LeaseState.RELEASING, LeaseState.OPEN),
        (LeaseState.RELEASED, LeaseState.OPEN),
        (LeaseState.RELEASED, LeaseState.RELEASING),
    ],
)
def test_any_other_edge_is_refused(current: LeaseState, new: LeaseState) -> None:
    with pytest.raises(InvalidTransitionError, match=current.value):
        transition(current, new)


def test_releasing_a_lease_records_why_and_repeats_harmlessly() -> None:
    lease = _lease(FakeClock())
    releasing = lease.releasing(ReleaseCause.DEAD_MAN)
    assert (lease.state, releasing.state) == (LeaseState.OPEN, LeaseState.RELEASING)
    assert releasing.release_cause is ReleaseCause.DEAD_MAN
    assert releasing.releasing(ReleaseCause.DEAD_MAN).state is LeaseState.RELEASING


def test_processes_are_tracked_by_pid_and_replaced_not_duplicated() -> None:
    lease = _lease(FakeClock())
    first = ProcessRecord(pid=10, created=1.0, kind=ProcessKind.EXEC)
    second = ProcessRecord(pid=10, created=2.0, kind=ProcessKind.BACKGROUND)
    with_both = lease.with_process(first).with_process(second)
    assert with_both.processes == (second,)
    assert with_both.without_process(10).processes == ()
    assert lease.processes == ()


def test_restore_records_keep_the_first_prior_state_of_a_path() -> None:
    lease = _lease(FakeClock())
    first = RestoreRecord(path="/etc/one", kind=RestoreKind.CREATED_FILE)
    again = RestoreRecord(path="/etc/one", kind=RestoreKind.REPLACED, backup="0.bak", mode=0o644)
    other = RestoreRecord(path="/etc/two", kind=RestoreKind.CREATED_DIR)
    changed = lease.with_restore((first,)).with_restore((again, other))
    assert [r.path for r in changed.restores] == ["/etc/one", "/etc/two"]
    assert changed.restores[0] is first or changed.restores[0] == first
    assert changed.is_recorded("/etc/two") and not changed.is_recorded("/etc/three")


def test_a_lease_round_trips_through_json() -> None:
    lease = _lease(FakeClock()).with_process(
        ProcessRecord(pid=7, created=3.5, kind=ProcessKind.EXEC)
    )
    lease = lease.with_restore((RestoreRecord(path="/x", kind=RestoreKind.CREATED_FILE),))
    again = DeviceLease.model_validate_json(
        lease.releasing(ReleaseCause.COMPLETED).model_dump_json()
    )
    assert again == lease.releasing(ReleaseCause.COMPLETED)


@pytest.mark.parametrize(
    "changes",
    [
        {"quota_bytes": -1},
        {"scratch_root": 5},
        {"lease_id": "task_01M4TJ4GG08H248H248H248H24"},
        {"surprise": True},
    ],
)
def test_a_lease_refuses_what_its_fields_refuse(changes: dict[str, object]) -> None:
    good = _lease(FakeClock()).model_dump(mode="json")
    with pytest.raises(ValidationError):
        DeviceLease.model_validate({**good, **changes})


def test_a_process_record_refuses_pid_zero_and_a_restore_record_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        ProcessRecord(pid=0, created=1.0, kind=ProcessKind.EXEC)
    with pytest.raises(ValidationError):
        RestoreRecord.model_validate({"path": "/x", "kind": "VANISHED"})
