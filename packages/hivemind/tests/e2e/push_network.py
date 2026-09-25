"""Stand in for the network a Hive's push deliveries cross, so none of them leaves the process.

A push delivery leaves the Hive Stand (the machine the Queen, the orchestrator, runs on) for a
program's webhook receiver or a browser vendor's Web Push service (ADR-0042). An end-to-end test
needs both ends without the internet: ``PushNetwork`` is one recording ``httpx.MockTransport`` that
plays every receiver, and one static table that plays DNS for their names, handed to ``hive
serve``'s composition through its own seams (``build_served_hive``'s ``push_transport`` and
``ServedHive.resolver``). The destination guard of the Hive Entrance (the Hive's one HTTP door)
still vets every URL against the addresses its name resolves to and pins the address it checked, so
each delivery is addressed to that address with the receiver's name in its ``Host`` header, which is
how ``to`` sorts them. ``delivered`` waits for a notice of one kind at one receiver, reading each
body the way that receiver would: a webhook's as it came, a Web Push body decrypted with the phone's
own key.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by e2e.entrance_stand and
    the phase 10 push tests under tests/e2e.

Key invariants:
    - Every delivery is recorded in the order it arrived and answered 201, like a receiver that
      stored it.

See Also:
    - unit.entrance.push.support for the recorder, the resolver and the names they answer for.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from unit.entrance.push.support import Recorder, StaticResolver

DELIVERY_WAIT_S = 20.0  # Generous: a goal's question reaches its receivers in about a second.
_POLL_S = 0.02  # How often a wait re-reads what the receivers were sent.

__all__ = ["DELIVERY_WAIT_S", "Opener", "PushNetwork", "as_sent"]

# Turns a delivery's body into the notice's JSON bytes, as its receiver reads it.
type Opener = Callable[[bytes], bytes]


def as_sent(body: bytes) -> bytes:
    """Read a webhook body as it came: the notice JSON, signed but not encrypted."""
    return body


@dataclass(frozen=True, slots=True)
class PushNetwork:
    """The receivers outside the Hive Stand and the DNS that names them, as one test sees them.

    Attributes:
        service: Records every delivery: the webhook receiver's and the push service's alike.
        resolver: Resolves the receivers' names from a table, never from DNS.
    """

    service: Recorder = field(default_factory=Recorder)
    resolver: StaticResolver = field(default_factory=StaticResolver)

    def transport(self) -> httpx.MockTransport:
        """Return the transport every push delivery goes out on: straight to ``service``."""
        return httpx.MockTransport(self.service)

    def to(self, host: str) -> list[httpx.Request]:
        """Return every delivery addressed to ``host`` so far, oldest first.

        Args:
            host: The receiver's name, as the delivery's ``Host`` header carries it.

        Returns:
            The recorded requests.
        """
        # A pinned delivery connects to the checked address; only Host still names the receiver.
        return [request for request in self.service.requests if request.headers["host"] == host]

    async def delivered(
        self, host: str, kind: str, ref: object = None, opener: Opener = as_sent
    ) -> tuple[dict[str, Any], httpx.Request]:
        """Wait until ``host`` was sent a ``kind`` notice (about ``ref``, when given).

        Args:
            host: The receiver's name.
            kind: The notice kind to wait for, e.g. ``question_waiting``.
            ref: The ref the notice must carry; any when None.
            opener: How the receiver reads a body: ``as_sent`` for a webhook, the phone's own
                decryption for Web Push.

        Returns:
            The first such notice and the delivery that carried it.

        Raises:
            TimeoutError: None arrived within ``DELIVERY_WAIT_S``.
        """
        # One deadline for the whole wait; the Hive delivers off its own tick, never on request.
        async with asyncio.timeout(DELIVERY_WAIT_S):
            while True:
                for request in self.to(host):
                    notice: dict[str, Any] = json.loads(opener(request.content))
                    if notice["kind"] == kind and (ref is None or notice["ref"] == ref):
                        return notice, request
                await asyncio.sleep(_POLL_S)
