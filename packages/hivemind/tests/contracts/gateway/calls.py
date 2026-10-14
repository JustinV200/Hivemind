"""Send a gateway under test requests as the Queen or the Warden, and read its refusals.

The conformance suite asserts what a gateway puts on the wire, so it cannot use `PollenSession`
(which turns frames into the `CellSession` a Worker sees and hides most of them). `Calls` is the
plumbing every request of the suite shares: the open `DeviceLink` of one gateway, the two addresses
a frame may come from (the Queen for leases and the Queen-only kinds, the Warden for session
traffic), a request that returns the frame answering it, a request that must be refused, and the
ordering barrier `sync`. Frames on one connection are handled in order, so once a later ping has
been answered, everything sent before it has been handled. The requests themselves are in
`leases`, `files` and `programs`, which build on this class.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. The base of
    `contracts.gateway.Driver`; builds on the swarm gateway's `DeviceLink`.

Key invariants:
    - Every request waits on a bounded timeout (`REPLY_TIMEOUT_S`); a gateway that never answers
      is a `DeviceTimeoutError`, not a hang.
    - A refusal comes back as the `DeviceRequestError` the link raises, whose `device_code` is the
      `control.error` code, so a test says which code a request draws.
    - `frames_of` reads only the frames this device sent, so several gateways may share one Hive
      Stand.

See Also:
    - docs/waggle/gateway-contract.md sections 8 and 9 for the messages sent through this.
    - hivemind.swarm.gateway.link for the link and the exchange the requests go through.
"""

from __future__ import annotations

from collections.abc import Awaitable

import pytest
from builders.swarm.live import LiveHive

from hivemind.swarm.errors import DeviceRequestError
from hivemind.swarm.gateway import DeviceLink
from hivemind.swarm.models import SwarmNode
from waggle.envelope import Envelope
from waggle.messages.base import WaggleMessage
from waggle.messages.control import Ping, Pong
from waggle.messages.session import SessionExit

REPLY_TIMEOUT_S = 30.0  # Real seconds the suite waits for a frame of an answer.

__all__ = ["REPLY_TIMEOUT_S", "Calls", "exit_of", "refused"]


class Calls:
    """The Hive Stand's line to one gateway: requests out, frames and refusals back."""

    def __init__(self, hive: LiveHive, node: SwarmNode) -> None:
        """Bind the line to a Hive Stand and the node its gateway enrolled as.

        Args:
            hive: The running Hive Stand, whose hub holds the gateway's link.
            node: The gateway's node as enrolled: its device, Cell and Warden ids.
        """
        self.hive = hive
        self.node = node

    @property
    def link(self) -> DeviceLink:
        """The gateway's open link; the test fails if the gateway is not connected."""
        link = self.hive.hub.link(self.node.device_id)
        assert link is not None, f"device {self.node.device_id} is not connected"
        return link

    @property
    def warden(self) -> str:
        """The address session traffic comes from."""
        return str(self.node.warden_id)

    @property
    def queen(self) -> str:
        """The address lease requests and the Queen-only kinds come from."""
        return self.link.hive_id

    def frames_of[PayloadT2: WaggleMessage](self, kind: type[PayloadT2]) -> list[Envelope]:
        """Every frame of one payload type this device sent unprompted, oldest first."""
        return [
            frame
            for frame in self.hive.log.frames
            if frame.sender == self.node.device_id and isinstance(frame.payload, kind)
        ]

    async def ask(
        self, payload: WaggleMessage, *, as_queen: bool = False, timeout_s: float = REPLY_TIMEOUT_S
    ) -> Envelope:
        """Send a request and return the frame that answers it.

        Raises:
            DeviceRequestError: The gateway answered with a `control.error`.
        """
        sender = self.queen if as_queen else self.warden
        return await self.link.request(payload, sender=sender, timeout_s=timeout_s)

    async def refusal(self, payload: WaggleMessage, *, as_queen: bool = False) -> str:
        """Send a request that must be refused and return the `control.error` code it drew."""
        try:
            answer = await self.ask(payload, as_queen=as_queen)
        except DeviceRequestError as refused_request:
            return refused_request.device_code
        raise AssertionError(f"the gateway answered {answer.kind}, not a refusal")

    async def ask_ping(self, *, as_queen: bool = True) -> Envelope:
        """Ping the gateway as the Queen or the Warden and return the pong's envelope."""
        answer = await self.ask(Ping(), as_queen=as_queen)
        assert isinstance(answer.payload, Pong)
        return answer

    async def sync(self) -> None:
        """Wait for a ping to be answered: every frame sent before it has been handled."""
        await self.ask_ping()


def exit_of(envelope: Envelope) -> SessionExit:
    """Return the `session.exit` an operation ended in, or fail the test."""
    assert isinstance(envelope.payload, SessionExit), f"expected an exit: {envelope.kind}"
    return envelope.payload


async def refused(call: Awaitable[object]) -> str:
    """Await a request that must be refused and return the `control.error` code it drew."""
    with pytest.raises(DeviceRequestError) as raised:
        await call
    return raised.value.device_code
