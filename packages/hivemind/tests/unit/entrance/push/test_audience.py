"""Tests for hivemind.entrance.push.audience: who hears which kind of notice (ADR-0042).

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/audience.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.audience for the module under test.
"""

from __future__ import annotations

import pytest
from builders.entrance import make_device
from unit.entrance.push.support import SteppingClock, approved_device

from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice
from hivemind.entrance.push import NoticeKind, audience_for
from hivemind.entrance.push.audience import held_capabilities

_PUSH = "entrance:push"  # Every device that hears anything holds this.


def _ids(*devices: EnrolledDevice) -> frozenset[str]:
    """The ids of ``devices``."""
    return frozenset(device.id for device in devices)


def test_question_waiting_reaches_only_devices_that_may_answer() -> None:
    clock = SteppingClock()
    answerer = approved_device(clock, _PUSH, "entrance:answer")
    submitter = approved_device(clock, _PUSH, "entrance:submit")

    audience = audience_for(NoticeKind.QUESTION_WAITING, [answerer, submitter])

    assert audience.kind is NoticeKind.QUESTION_WAITING
    assert audience.device_ids == _ids(answerer)


def test_reply_waiting_reaches_only_devices_that_may_read_the_chat() -> None:
    clock = SteppingClock()
    reader = approved_device(clock, _PUSH, "entrance:submit", "honey:clearance:c2")
    low_clearance = approved_device(clock, _PUSH, "entrance:submit", "honey:clearance:c1")
    cannot_submit = approved_device(clock, _PUSH, "honey:clearance:c2")

    audience = audience_for(NoticeKind.REPLY_WAITING, [reader, low_clearance, cannot_submit])

    assert audience.device_ids == _ids(reader)


def test_goal_completed_reaches_only_the_submitting_device() -> None:
    clock = SteppingClock()
    submitter = approved_device(clock, _PUSH, "entrance:submit")
    other = approved_device(clock, _PUSH, "entrance:submit")

    audience = audience_for(NoticeKind.GOAL_COMPLETED, [submitter, other], submitter=submitter.id)

    assert audience.device_ids == _ids(submitter)


def test_goal_completed_needs_its_submitting_device() -> None:
    with pytest.raises(ValueError, match="submitter"):
        audience_for(NoticeKind.GOAL_COMPLETED, [])


@pytest.mark.parametrize("kind", [NoticeKind.SECURITY_EVENT, NoticeKind.ALARM_WAITING])
def test_security_events_and_alarms_reach_every_device_but_the_one_concerned(
    kind: NoticeKind,
) -> None:
    clock = SteppingClock()
    first, second, concerned = (approved_device(clock) for _ in range(3))

    audience = audience_for(kind, [first, second, concerned], concerning=concerned.id)

    assert audience.device_ids == _ids(first, second)


def test_a_security_event_about_no_device_reaches_every_approved_device() -> None:
    clock = SteppingClock()
    devices = [approved_device(clock) for _ in range(3)]

    audience = audience_for(NoticeKind.SECURITY_EVENT, devices)

    assert audience.device_ids == _ids(*devices)


@pytest.mark.parametrize(
    "status", [DeviceStatus.INVITED, DeviceStatus.PENDING, DeviceStatus.LOCKED, DeviceStatus.DENIED]
)
def test_a_device_that_is_not_approved_hears_nothing(status: DeviceStatus) -> None:
    device = make_device(SteppingClock(), status)
    capable = device.model_copy(update={"capabilities": (_PUSH, "entrance:answer")})

    audience = audience_for(NoticeKind.SECURITY_EVENT, [capable])

    assert audience.device_ids == frozenset()


@pytest.mark.parametrize("kind", [kind for kind in NoticeKind if kind is not NoticeKind.WITHDRAWN])
def test_a_device_without_entrance_push_hears_nothing(kind: NoticeKind) -> None:
    clock = SteppingClock()
    everything_but_push = approved_device(
        clock, "entrance:answer", "entrance:submit", "honey:clearance:c2"
    )

    audience = audience_for(kind, [everything_but_push], submitter=everything_but_push.id)

    assert audience.device_ids == frozenset()


def test_withdrawn_never_has_an_audience_of_its_own() -> None:
    clock = SteppingClock()
    device = approved_device(clock, _PUSH, "entrance:answer", "entrance:submit")

    audience = audience_for(NoticeKind.WITHDRAWN, [device], submitter=device.id)

    assert audience.device_ids == frozenset()


def test_a_device_with_an_unparseable_capability_holds_nothing() -> None:
    # The model accepts any lowercase-led string; the guard's grammar is what refuses this one.
    device = approved_device(SteppingClock(), _PUSH, "entrance:answer", "bogus:grant")

    audience = audience_for(NoticeKind.QUESTION_WAITING, [device])

    assert audience.device_ids == frozenset()
    assert len(held_capabilities(device)) == 0
