"""Tests for hivemind.entrance.push.dispatch.refs: per-ref locks and live recipients.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/dispatch/refs.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.dispatch.refs for the module under test.
"""

from __future__ import annotations

import asyncio

from unit.entrance.push.support import SteppingClock

from hivemind.entrance.push.dispatch import LiveRecipients, RefLocks
from waggle.ids import new_device_id


async def test_a_ref_lock_exists_only_while_held() -> None:
    locks = RefLocks()

    async with locks.hold("msg_a"):
        assert len(locks) == 1

    assert len(locks) == 0


async def test_two_holders_of_one_ref_take_turns_and_other_refs_do_not_wait() -> None:
    locks = RefLocks()
    order: list[str] = []
    release = asyncio.Event()

    async def first() -> None:
        async with locks.hold("msg_a"):
            order.append("first in")
            await release.wait()
            order.append("first out")

    async def second() -> None:
        async with locks.hold("msg_a"):
            order.append("second in")

    async with asyncio.TaskGroup() as group:
        group.create_task(first())
        await asyncio.sleep(0)
        group.create_task(second())
        async with locks.hold("msg_b"):
            order.append("other ref")
        release.set()

    assert order == ["first in", "other ref", "first out", "second in"]
    assert len(locks) == 0


def test_live_recipients_merge_and_are_taken_once() -> None:
    clock = SteppingClock()
    phone, laptop = new_device_id(clock), new_device_id(clock)
    recipients = LiveRecipients(capacity=8)

    recipients.add("msg_a", frozenset({phone}))
    recipients.add("msg_a", frozenset({laptop}))
    recipients.add("msg_b", frozenset())

    assert recipients.take("msg_a") == frozenset({phone, laptop})
    assert recipients.take("msg_a") == frozenset()
    assert len(recipients) == 0


def test_live_recipients_forget_the_oldest_refs_beyond_capacity() -> None:
    device = new_device_id(SteppingClock())
    recipients = LiveRecipients(capacity=2)

    for ref in ("msg_a", "msg_b", "msg_c"):
        recipients.add(ref, frozenset({device}))

    assert len(recipients) == 2
    assert recipients.take("msg_a") == frozenset()
    assert recipients.take("msg_c") == frozenset({device})
