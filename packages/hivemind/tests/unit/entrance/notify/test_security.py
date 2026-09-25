"""Test hivemind.entrance.notify.security: security events reach every other device.

Fits into the Hive:
    Mirrors src/hivemind/entrance/notify/security.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from unit.entrance.notify.support import running_outbox
from unit.entrance.push.support import SteppingClock, approved_device

from hivemind.entrance.enrol import SecurityNotice
from hivemind.entrance.notify import PushSecurityNotifier
from hivemind.entrance.push import NoticeKind
from waggle.ids import EventId, new_event_id


async def test_a_devices_security_event_reaches_every_other_device() -> None:
    clock = SteppingClock()
    locked, other = approved_device(clock), approved_device(clock)
    event_id = new_event_id(clock)
    notice = SecurityNotice(
        device_id=locked.id, event_id=event_id, kind="guard.entrance_locked", at=clock.now()
    )
    async with running_outbox([locked, other]) as rig:
        await PushSecurityNotifier(rig.outbox).notify(notice)
        await rig.outbox.join()

    [(sent, subscription)] = rig.channel.deliveries
    assert (sent.kind, sent.ref) == (NoticeKind.SECURITY_EVENT, event_id)
    assert subscription.device_id == other.id


async def test_a_broadcast_reaches_every_device() -> None:
    clock = SteppingClock()
    devices = [approved_device(clock), approved_device(clock)]
    notice = SecurityNotice.broadcast(
        EventId(new_event_id(clock)), "guard.entrance_reduced", clock.now()
    )
    async with running_outbox(devices) as rig:
        await PushSecurityNotifier(rig.outbox).notify(notice)
        await rig.outbox.join()

    assert notice.is_broadcast
    assert {subscription.device_id for _, subscription in rig.channel.deliveries} == {
        device.id for device in devices
    }
