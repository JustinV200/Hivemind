"""Tests for hivemind.entrance.enrol.deps.fake: the seams' recording fakes.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/deps/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.deps.fake for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hivemind.entrance.enrol import (
    DeviceStatus,
    FakeGoalLedger,
    RecordingDeviceOffboarder,
    RecordingSecurityNotifier,
    SecurityNotice,
)
from waggle.ids import DeviceId, EventId, TaskId

_DEVICE = DeviceId("device_01M221E4C10R4XDPNQNRX85AAA")
_OTHER = DeviceId("device_01M221E4C10R4XDPNQNRX85AAB")
_GOALS = (TaskId("task_01M221E4C10R4XDPNQNRX85AAA"), TaskId("task_01M221E4C10R4XDPNQNRX85AAB"))


async def test_the_recording_fakes_keep_every_call_in_order() -> None:
    notifier, offboarder = RecordingSecurityNotifier(), RecordingDeviceOffboarder()
    notice = SecurityNotice(
        _DEVICE,
        EventId("event_01M221E4C10R4XDPNQNRX85AAA"),
        "guard.entrance_locked",
        datetime(2026, 9, 24, tzinfo=UTC),
    )

    await notifier.notify(notice)
    await offboarder.offboard(_DEVICE, DeviceStatus.LOCKED)
    await offboarder.offboard(_DEVICE, DeviceStatus.REVOKED)

    assert notifier.notices == [notice]
    assert offboarder.offboarded == [
        (_DEVICE, DeviceStatus.LOCKED),
        (_DEVICE, DeviceStatus.REVOKED),
    ]


async def test_the_fake_ledger_cancels_only_open_cancellable_goals() -> None:
    finished = TaskId("task_01M221E4C10R4XDPNQNRX85AAC")
    unknown = TaskId("task_01M221E4C10R4XDPNQNRX85AAD")
    ledger = FakeGoalLedger({_OTHER: (finished,)}, finishing=frozenset({_GOALS[1]}))
    ledger.submit(_DEVICE, _GOALS)

    cancelled = await ledger.cancel_goals((*_GOALS, unknown), "why")

    assert cancelled == (_GOALS[0],)
    assert await ledger.open_goals(_DEVICE) == (_GOALS[1],)
    assert await ledger.open_goals(_OTHER) == (finished,)
    assert ledger.cancellations == [((_GOALS[0],), "why")]
