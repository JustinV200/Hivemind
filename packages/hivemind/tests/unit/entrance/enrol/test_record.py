"""Tests for hivemind.entrance.enrol.record: one edge with its event, then cut off and notify.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/record.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.record for the module under test.
"""

from __future__ import annotations

import itertools

import pytest
from builders.entrance import Enrolment, admitted, memory_enrolment, mint
from pydantic import ValidationError

from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.enrol.record import (
    MAX_IDS_ON_TRAIL,
    MAX_REASON_CHARS,
    Transition,
    apply_transition,
    iso,
    named_ids,
    reason_text,
)
from hivemind.entrance.errors import InvalidDeviceTransitionError

_S = DeviceStatus


@pytest.fixture
def rig() -> Enrolment:
    """An enrolment rig over in-memory tables."""
    return memory_enrolment()


async def test_a_transition_moves_the_device_with_its_event_then_notifies(rig: Enrolment) -> None:
    minted = await mint(rig)
    transition = Transition(minted.device_id, _S.INVITED, _S.EXPIRED, "system", {"why": "test"})

    moved = await apply_transition(rig.deps, transition)

    assert moved.status is _S.EXPIRED
    event = (await rig.events())[-1]
    assert (event.kind, event.subject_id, event.actor, event.payload) == (
        "guard.entrance_expired",
        minted.device_id,
        "system",
        {"why": "test"},
    )
    notice = rig.notifier.notices[-1]
    assert (notice.device_id, notice.event_id, notice.kind, notice.at) == (
        minted.device_id,
        event.id,
        event.kind,
        event.at,
    )
    assert rig.offboarder.offboarded == []


@pytest.mark.parametrize(
    ("path", "offboarded"),
    [
        ((_S.APPROVED, _S.LOCKED), [_S.LOCKED]),
        ((_S.APPROVED, _S.LOCKED, _S.APPROVED), [_S.LOCKED]),
        ((_S.APPROVED, _S.EXPIRED), [_S.EXPIRED]),
        ((_S.APPROVED, _S.LOCKED, _S.REVOKED), [_S.LOCKED, _S.REVOKED]),
    ],
)
async def test_leaving_an_approval_cuts_the_device_off_and_an_unlock_does_not(
    rig: Enrolment, path: tuple[DeviceStatus, ...], offboarded: list[DeviceStatus]
) -> None:
    device = await admitted(rig)

    for expected, new in itertools.pairwise(path):
        await apply_transition(rig.deps, Transition(device.id, expected, new, "human", {}))

    assert rig.offboarder.offboarded == [(device.id, reason) for reason in offboarded]


async def test_an_impossible_edge_or_a_bad_actor_writes_nothing(rig: Enrolment) -> None:
    minted = await mint(rig)
    recorded = await rig.events()

    with pytest.raises(InvalidDeviceTransitionError):
        await apply_transition(
            rig.deps, Transition(minted.device_id, _S.INVITED, _S.APPROVED, "human", {})
        )
    with pytest.raises(ValidationError):
        await apply_transition(
            rig.deps, Transition(minted.device_id, _S.INVITED, _S.REVOKED, "somebody", {})
        )
    assert await rig.events() == recorded
    assert (await rig.store.get_device(minted.device_id)).status is _S.INVITED


def test_payload_helpers_keep_events_bounded() -> None:
    ids = [f"task_{index}" for index in range(MAX_IDS_ON_TRAIL + 5)]

    assert named_ids(ids) == ids[:MAX_IDS_ON_TRAIL]
    assert iso(None) is None
    assert reason_text("not mine") == "not mine"
    with pytest.raises(ValidationError):
        reason_text("x" * (MAX_REASON_CHARS + 1))
