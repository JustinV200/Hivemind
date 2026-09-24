"""Test helpers for hivemind.entrance.notify: an outbox over a fake channel, running in a group.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5) local to this test package, not shipped: the
    notify tests need a real ``PushOutbox`` and ``PushDispatcher`` whose one stored channel is a
    recording ``FakePush``, over a fixed table of devices, run for the length of a block.

Key invariants:
    - None: this module holds test helpers only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from unit.entrance.push.support import HOOK_URL, SteppingClock, make_guard

from hivemind.entrance.enrol import EnrolledDevice
from hivemind.entrance.notify import PushOutbox
from hivemind.entrance.push import (
    Admission,
    ChannelKind,
    FakePush,
    LivePush,
    MemorySubscriptionStore,
    PushChannel,
    PushChannels,
    PushDispatcher,
)
from hivemind.manifest.schema import EntrancePushSection

__all__ = ["Devices", "OutboxRig", "running_outbox"]


class Devices:
    """A DeviceSource over a fixed list."""

    def __init__(self, devices: Sequence[EnrolledDevice]) -> None:
        """Hold the devices."""
        self._devices = tuple(devices)

    async def list_devices(self) -> tuple[EnrolledDevice, ...]:
        """Return every device; see DeviceSource.list_devices."""
        return self._devices


@dataclass(frozen=True, slots=True)
class OutboxRig:
    """A running outbox and what a test inspects."""

    outbox: PushOutbox
    dispatcher: PushDispatcher
    channel: FakePush
    clock: SteppingClock


@asynccontextmanager
async def running_outbox(
    devices: Sequence[EnrolledDevice],
    channel: PushChannel | None = None,
    capacity: int = 64,
    start: bool = True,
) -> AsyncIterator[OutboxRig]:
    """Run an outbox over ``devices``, each subscribed by webhook, until the block exits.

    Args:
        devices: The approved devices; each gets one webhook subscription.
        channel: The webhook channel; a fresh FakePush when omitted.
        capacity: The outbox's queue bound.
        start: Whether to run the outbox (False: jobs only queue).

    Yields:
        The rig.
    """
    clock = SteppingClock()
    fake = channel if isinstance(channel, FakePush) else FakePush()
    stored: dict[ChannelKind, PushChannel] = {ChannelKind.WEBHOOK: channel or fake}
    admission = Admission(EntrancePushSection(), make_guard())
    dispatcher = PushDispatcher(
        PushChannels(live=LivePush(), stored=stored), MemorySubscriptionStore(), admission, clock
    )
    for device in devices:
        await dispatcher.register(device, ChannelKind.WEBHOOK, HOOK_URL)
    outbox = PushOutbox(dispatcher, Devices(devices), clock, capacity)
    async with asyncio.TaskGroup() as group:
        task = group.create_task(outbox.run()) if start else None
        try:
            yield OutboxRig(outbox, dispatcher, fake, clock)
        finally:
            if task is not None:
                task.cancel()
