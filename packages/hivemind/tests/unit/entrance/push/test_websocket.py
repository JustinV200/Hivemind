"""Tests for hivemind.entrance.push.websocket: LivePush, the live push socket hub.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/websocket.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.websocket for the module under test.
"""

from __future__ import annotations

from unit.entrance.push.support import SteppingClock

from hivemind.entrance.push import (
    DeliveryOutcome,
    LivePush,
    LiveSocketClosedError,
    NoticeKind,
    PushNotice,
    notice_json,
)
from waggle.ids import new_device_id

_REF = "msg_01J8ZQ7X9K3M2N4P5Q6R7S8T9V"  # The question every notice here points at.


class _Socket:
    """A live sender that records frames, or fails with a given error."""

    def __init__(self, error: Exception | None = None) -> None:
        """Build a socket that fails with ``error`` on every send, or records frames when None."""
        self.frames: list[str] = []
        self._error = error

    async def send(self, frame: str) -> None:
        """Write one frame, or raise this socket's error."""
        if self._error is not None:
            raise self._error
        self.frames.append(frame)


def _notice() -> PushNotice:
    """Mint a question notice at the test's fixed time."""
    return PushNotice.mint(NoticeKind.QUESTION_WAITING, _REF, SteppingClock())


async def test_deliver_sends_the_notice_json_to_every_socket_of_the_device() -> None:
    hub = LivePush()
    device = new_device_id(SteppingClock())
    phone, laptop = _Socket(), _Socket()
    hub.attach(device, phone.send)
    hub.attach(device, laptop.send)
    notice = _notice()

    outcome = await hub.deliver(notice, device)

    assert outcome is DeliveryOutcome.DELIVERED
    assert phone.frames == laptop.frames == [notice_json(notice).decode()]


async def test_deliver_reaches_no_other_device() -> None:
    clock = SteppingClock()
    hub = LivePush()
    other = _Socket()
    hub.attach(new_device_id(clock), other.send)

    outcome = await hub.deliver(_notice(), new_device_id(clock))

    assert outcome is DeliveryOutcome.GONE
    assert other.frames == []


async def test_a_sender_that_raises_is_detached_and_the_others_still_deliver() -> None:
    hub = LivePush()
    device = new_device_id(SteppingClock())
    closed = _Socket(LiveSocketClosedError("gone"))
    reset = _Socket(ConnectionResetError("reset"))
    stuck = _Socket(TimeoutError())
    after_close = _Socket(RuntimeError('Cannot call "send" once a close message has been sent.'))
    healthy = _Socket()
    for socket in (closed, reset, stuck, after_close, healthy):
        hub.attach(device, socket.send)

    first = await hub.deliver(_notice(), device)
    second = await hub.deliver(_notice(), device)

    assert first is second is DeliveryOutcome.DELIVERED
    assert len(healthy.frames) == 2
    assert hub.live_devices() == frozenset({device})


async def test_a_device_whose_every_sender_fails_is_gone_and_forgotten() -> None:
    hub = LivePush()
    device = new_device_id(SteppingClock())
    hub.attach(device, _Socket(LiveSocketClosedError("gone")).send)

    outcome = await hub.deliver(_notice(), device)

    assert outcome is DeliveryOutcome.GONE
    assert hub.live_devices() == frozenset()


async def test_detach_removes_exactly_that_sender_and_is_idempotent() -> None:
    hub = LivePush()
    device = new_device_id(SteppingClock())
    kept, dropped = _Socket(), _Socket()
    hub.attach(device, kept.send)
    attachment = hub.attach(device, dropped.send)

    attachment.detach()
    attachment.detach()
    await hub.deliver(_notice(), device)

    assert len(kept.frames) == 1
    assert dropped.frames == []


async def test_detach_device_drops_every_sender_of_that_device() -> None:
    clock = SteppingClock()
    hub = LivePush()
    device = new_device_id(clock)
    hub.attach(device, _Socket().send)
    hub.attach(device, _Socket().send)

    dropped = hub.detach_device(device)

    assert dropped == 2
    assert hub.detach_device(device) == 0
    assert await hub.deliver(_notice(), device) is DeliveryOutcome.GONE


async def test_last_detach_removes_the_device_from_the_live_set() -> None:
    hub = LivePush()
    device = new_device_id(SteppingClock())
    attachment = hub.attach(device, _Socket().send)

    attachment.detach()

    assert hub.live_devices() == frozenset()
