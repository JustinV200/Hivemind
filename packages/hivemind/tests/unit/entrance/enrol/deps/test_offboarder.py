"""Tests for hivemind.entrance.enrol.deps.offboarder: the no-op offboarder.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/deps/offboarder.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.deps.offboarder for the module under test.
"""

from __future__ import annotations

from hivemind.entrance.enrol import DeviceOffboarder, DeviceStatus, NullDeviceOffboarder
from waggle.ids import DeviceId


async def test_the_no_op_offboarder_accepts_any_device_and_keeps_nothing() -> None:
    offboarder: DeviceOffboarder = NullDeviceOffboarder()

    await offboarder.offboard(DeviceId("device_01M221E4C10R4XDPNQNRX85AAA"), DeviceStatus.REVOKED)

    assert vars(offboarder) == {}
