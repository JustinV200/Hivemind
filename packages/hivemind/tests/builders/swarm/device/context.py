"""Give the scripted device's services one way to speak: replies correlated to a request.

Each service of the scripted device (leases, execs, files, processes) answers requests the same
two ways: with a message correlated to the request, or with a `control.error` correlated to it.
`DeviceContext` is that, plus the device's `DeviceState`, so a service takes one argument and never
learns how a frame is addressed, signed or sent.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Built by the scripted device and
    handed to each of its services.

Key invariants:
    - Every reply carries the request's envelope id as its correlation id.
    - An error is always a full sentence with a stable code, as the gateway contract requires.

See Also:
    - builders.swarm.device.core for the device that builds the context.
    - docs/waggle/gateway-contract.md section 15 for the error codes.
"""

from __future__ import annotations

from typing import Protocol

from builders.swarm.device.state import DeviceState

from waggle.envelope import Envelope
from waggle.ids import MessageId
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage

__all__ = ["DeviceContext", "Send"]

DEFAULT_ERROR_MESSAGE = "The scripted device refused the request."


class Send(Protocol):
    """Send one frame from the device to the Hive."""

    async def __call__(
        self, payload: WaggleMessage, correlation_id: MessageId | None = None
    ) -> None:
        """Send `payload`, correlated to `correlation_id` when it follows from a request."""
        ...


class DeviceContext:
    """The device's state and its one way to answer."""

    def __init__(self, state: DeviceState, send: Send) -> None:
        """Bundle the state with the device's sender.

        Args:
            state: What the device holds.
            send: Sends one frame to the Hive.
        """
        self.state = state
        self._send = send

    async def reply(self, request: Envelope, payload: WaggleMessage) -> None:
        """Answer `request` with `payload`, correlated to it."""
        await self._send(payload, request.id)

    async def error(
        self, request: Envelope, code: str, message: str = DEFAULT_ERROR_MESSAGE
    ) -> None:
        """Answer `request` with a `control.error` carrying `code`."""
        await self._send(
            ErrorMessage(code=code, message=message, failed_kind=request.kind, is_retryable=False),
            request.id,
        )
