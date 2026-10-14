"""Define Arrivals: where a colonization waits for the Warden it started on a device to dial in.

Colonizing a device ends with a process the packet launched dialling the swarm listener. The
listener hands that connection to the swarm gate's door, which attaches the Warden to the Queen,
and the flow that asked for it is somewhere else entirely, waiting. `Arrivals` is the meeting
place. The flow registers that it expects a device's Warden before it sends START, so a Warden
that dials in quickly cannot be turned away for arriving early; the door asks whether a Level 0
device's Warden is expected, because at Level 0 the Warden is on the Hive Stand and a second one
dialling in for the same Cell is refused; and the door reports the attach, which wakes the flow.
Anything that is not expected is a stranger claiming to be that Warden, and is refused.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Written by `.promote` (expect, wait, forget) and read
    by the gate's door through the `Colonizer`. Calls into `hivemind.queen.deps` (the link type,
    for typing only), `hivemind.swarm` and waggle only.

Key invariants:
    - A device is expected from before START is sent until its flow ends: no window where a
      Warden that is on its way is unexpected.
    - A wait is bounded by the caller's timeout and fails with a stable word, never forever.
    - Only the first arrival counts; a second report for the same device is ignored.
    - Forgetting a device that was never expected, or twice, is not an error.

See Also:
    - hivemind.queen.swarm_gate.attach for the door that reports an arrival.
    - hivemind.queen.swarm_gate.colonize.promote for the flow that waits.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from waggle.ids import DeviceId

if TYPE_CHECKING:
    from hivemind.queen.deps import WardenLink

__all__ = ["Arrivals"]


class Arrivals:
    """Track the devices whose Warden is on its way, and wake the flow waiting for each."""

    def __init__(self) -> None:
        """Build an empty table: no device is expected."""
        self._waiting: dict[DeviceId, asyncio.Future[WardenLink]] = {}

    def expect(self, device_id: DeviceId) -> None:
        """Start expecting `device_id`'s Warden; call before the order that launches it.

        Args:
            device_id: The device being colonized.
        """
        self._waiting[device_id] = asyncio.get_running_loop().create_future()

    def expects(self, device_id: DeviceId) -> bool:
        """Whether `device_id`'s Warden is on its way and has not arrived yet."""
        future = self._waiting.get(device_id)
        return future is not None and not future.done()

    def arrived(self, device_id: DeviceId, link: WardenLink) -> None:
        """Report that `device_id`'s Warden attached under `link`, waking the flow waiting.

        Args:
            device_id: The device whose Warden attached.
            link: The Queen's end of the Warden's new link.
        """
        future = self._waiting.get(device_id)
        if future is not None and not future.done():
            future.set_result(link)

    async def wait(self, device_id: DeviceId, timeout_s: float) -> WardenLink:
        """Wait for `device_id`'s Warden to attach, and return its link.

        Args:
            device_id: The device, which must have been expected.
            timeout_s: Real seconds to wait for the Warden to dial in.

        Returns:
            The Queen's end of the Warden's link.

        Raises:
            ColonizeStepError: The device was never expected, or its Warden did not attach in
                time (`warden_never_attached`).
        """
        future = self._waiting.get(device_id)
        if future is None:
            raise ColonizeStepError("warden_never_attached")
        try:
            # Network-bound: the packet launched a process that dials the listener; the bound is
            # how long the Queen waits for it before she undoes the move.
            async with asyncio.timeout(timeout_s):
                return await future
        except TimeoutError as error:
            raise ColonizeStepError("warden_never_attached") from error

    def forget(self, device_id: DeviceId) -> None:
        """Stop expecting `device_id`'s Warden; a Warden that dials in now is refused."""
        future = self._waiting.pop(device_id, None)
        if future is not None and not future.done():
            future.cancel()
