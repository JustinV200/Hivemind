"""Hold every live push socket per device, and send a notice to each socket of a device.

A device with the Observation Hive or an app open keeps an authenticated ``/v1/push/stream``
WebSocket (ADR-0042); a notice for that device is one text frame of ``notice_json`` on every socket
it has open. ``LivePush`` is the in-memory hub of those sockets: the route that authenticates a
socket attaches a sender (an async callable that writes one frame) and keeps the returned
``LiveAttachment`` to detach it when the socket closes; ``deliver`` writes the frame to every
sender of the device, each bounded by a timeout, and detaches any sender that fails, so a dead
socket never costs a second timeout. Nothing here is persisted: a live socket does not outlive the
process, and a device that reconnects fetches what it missed over its session. The route that
attaches senders arrives with the Landing Board's routes (roadmap 10.5).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push``. Senders are
    attached by the push stream route; ``deliver`` is called by
    ``hivemind.entrance.push.dispatch.PushDispatcher``, which also detaches every sender of a
    device that is forgotten. Calls into the senders it holds and the push records only.

Key invariants:
    - A sender that raises ``LiveSocketClosedError``, an ``OSError`` (a timeout included) or a
      ``RuntimeError`` (a send after close) is detached before ``deliver`` returns.
    - ``detach`` of a ``LiveAttachment`` is idempotent and removes exactly that sender.
    - No lock is needed: attach and detach run without an ``await``, so on one event loop no two
      can interleave, and ``deliver`` sends to a snapshot of the senders.

See Also:
    - docs/adr/0042-landing-board-versioning-and-push.md for the live channel.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for how a push socket
      authenticates and why it closes when its session ends.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from hivemind.common.logging import get_logger
from hivemind.entrance.push.errors import LiveSocketClosedError
from hivemind.entrance.push.models import DeliveryOutcome, PushNotice, notice_json
from waggle.ids import DeviceId

LIVE_SEND_TIMEOUT_S = 5.0  # Writing one small frame is instant; five seconds means a stuck socket.

LiveSender = Callable[[str], Awaitable[None]]  # Writes one text frame to one live socket.

log = get_logger(__name__)

__all__ = ["LIVE_SEND_TIMEOUT_S", "LiveAttachment", "LivePush", "LiveSender"]


class LiveAttachment:
    """The handle ``LivePush.attach`` returns: ``detach`` removes exactly that sender, once."""

    def __init__(self, detach: Callable[[], None]) -> None:
        """Wrap the hub's removal of one sender.

        Args:
            detach: Removes the sender; safe to call more than once.
        """
        self._detach = detach

    def detach(self) -> None:
        """Remove the sender from the hub; a second call does nothing."""
        self._detach()


class LivePush:
    """The in-memory hub of live push sockets, keyed by device."""

    def __init__(self) -> None:
        """Create an empty hub."""
        # device -> (token -> sender); a token tells two sockets of one device apart.
        self._senders: dict[DeviceId, dict[int, LiveSender]] = {}
        self._next_token = 0

    def attach(self, device_id: DeviceId, send: LiveSender) -> LiveAttachment:
        """Add a live socket's sender for ``device_id``.

        Args:
            device_id: The authenticated device the socket belongs to.
            send: Writes one text frame; raises ``LiveSocketClosedError`` (or an OSError, or
                a RuntimeError) when the socket is gone.

        Returns:
            The handle the route calls ``detach`` on when the socket closes.
        """
        self._next_token += 1
        token = self._next_token
        self._senders.setdefault(device_id, {})[token] = send
        return LiveAttachment(lambda: self._detach(device_id, token))

    def live_devices(self) -> frozenset[DeviceId]:
        """Return every device with at least one live socket attached.

        Returns:
            The device ids, in no particular order.
        """
        return frozenset(self._senders)

    def detach_device(self, device_id: DeviceId) -> int:
        """Detach every sender of ``device_id``, as when the device leaves APPROVED.

        Args:
            device_id: The device to drop.

        Returns:
            How many senders were attached for it.
        """
        return len(self._senders.pop(device_id, {}))

    async def deliver(self, notice: PushNotice, device_id: DeviceId) -> DeliveryOutcome:
        """Send ``notice`` as one text frame to every live socket of ``device_id``.

        Args:
            notice: The notice.
            device_id: The device to reach.

        Returns:
            DELIVERED when at least one socket took the frame; GONE when the device has no live
            socket, or every one failed and was detached.
        """
        senders = list(self._senders.get(device_id, {}).items())
        if not senders:
            return DeliveryOutcome.GONE
        frame = notice_json(notice).decode("utf-8")
        # All of a device's sockets at once, so one stuck socket delays none of the others.
        async with asyncio.TaskGroup() as group:
            sends = [(token, group.create_task(_send(send, frame))) for token, send in senders]
        # Every sender that failed is dropped now; the socket's own close would detach it later.
        for token, task in sends:
            if not task.result():
                log.debug("push.live_sender_detached", device_id=device_id)
                self._detach(device_id, token)
        if any(task.result() for _, task in sends):
            return DeliveryOutcome.DELIVERED
        return DeliveryOutcome.GONE

    def _detach(self, device_id: DeviceId, token: int) -> None:
        """Remove one sender, and the device's entry once it has none left."""
        senders = self._senders.get(device_id)
        if senders is None:
            return
        senders.pop(token, None)
        if not senders:
            del self._senders[device_id]


async def _send(send: LiveSender, frame: str) -> bool:
    """Write one frame through one sender; report whether the socket took it."""
    try:
        # Latency: one frame on a local socket buffer; a timeout means the socket is stuck.
        async with asyncio.timeout(LIVE_SEND_TIMEOUT_S):
            await send(frame)
    except (LiveSocketClosedError, OSError, RuntimeError):
        # OSError covers a reset connection and the TimeoutError above; RuntimeError is what
        # Starlette raises for a send on a socket that already closed.
        return False
    return True
