"""Tests for hivemind.entrance.auth.limits.denials: a burst of capability denials locks a device.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/limits/denials.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.limits.denials for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.entrance import admitted_program, auth_rig, memory_enrolment, program_login

from hivemind.entrance.auth import DenialCounter, EndReason
from hivemind.entrance.enrol import DeviceStatus
from hivemind.manifest import EntranceSection

_WINDOW = timedelta(seconds=60)


async def test_a_burst_of_denials_inside_the_window_locks_the_device() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    counter = DenialCounter(auth.deps.enrolment, threshold=3, window=_WINDOW)

    results = [await counter.record(device.id) for _ in range(3)]

    assert results == [False, False, True]
    assert (await auth.store.get_device(device.id)).status is DeviceStatus.LOCKED
    (locked,) = await auth.enrolment.events("guard.entrance_locked")
    assert (locked.actor, locked.payload) == ("system", {"reason": "denial_burst"})
    ended = await auth.store.sessions.get(opened.session.token_hash)
    assert ended is not None
    assert ended.end_reason is EndReason.LOCKED


async def test_denials_spread_wider_than_the_window_never_lock() -> None:
    rig = memory_enrolment()
    device, _ = await admitted_program(rig)
    counter = DenialCounter(rig.deps, threshold=3, window=_WINDOW)

    for _ in range(6):
        assert not await counter.record(device.id)
        rig.clock.advance(31)

    assert (await rig.store.get_device(device.id)).status is DeviceStatus.APPROVED


async def test_a_device_already_locked_is_left_to_that_decision() -> None:
    rig = memory_enrolment()
    device, _ = await admitted_program(rig)
    counter = DenialCounter(rig.deps, threshold=1, window=_WINDOW)
    await counter.record(device.id)

    again = await counter.record(device.id)

    assert not again
    assert len(await rig.events("guard.entrance_locked")) == 1


async def test_the_window_starts_again_after_a_lock() -> None:
    rig = memory_enrolment()
    first, _ = await admitted_program(rig)
    second, _ = await admitted_program(rig)
    counter = DenialCounter.from_section(
        rig.deps, EntranceSection(lockout_denials=2, lockout_denial_window_s=60.0)
    )

    locks = [await counter.record(device) for device in (first.id, second.id, first.id)]

    assert locks == [False, False, True]
    assert (await rig.store.get_device(second.id)).status is DeviceStatus.APPROVED


def test_a_threshold_capacity_or_window_that_cannot_work_is_refused() -> None:
    rig = memory_enrolment()

    for threshold, window, capacity in ((0, _WINDOW, 1), (1, timedelta(0), 1), (1, _WINDOW, 0)):
        with pytest.raises(ValueError, match="threshold"):
            DenialCounter(rig.deps, threshold, window, capacity)
