"""Define NucDesk: what the gate hears from device Wardens about their model servers.

A colonized device's Warden announces itself with a capacity report that names the model servers
its own manifest declares (it starts none until the Queen promotes the device). The Queen needs
that list to decide a promotion, since she can only start what the operator put on the device, and
she keeps it here, per device, from the report's frames as they pass the gate's door. The desk also
carries the one question she asks a Warden that expects an answer: `ask` sends a
`swarm.nuc_promote` over the Warden's link and waits, bounded, for the `swarm.nuc_promoted` that
answers it. The answer is claimed by the envelope id of the order it is correlated to, and is the
gate's, not the Queen's: it is consumed at the door so it never reaches her inbox, where a kind
her autopilot has no rule for would wake a model. A Warden speaks only for its own device, so a
reply that names another, or answers nothing anyone waits on, is dropped and logged.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize.nuc`. Built by `hivemind.queen.swarm_gate.colonize.flow.
    Colonizer`; fed by the gate's door (`heard`, `lost`) and used by the Nuc flows (`ask`). Calls
    into `hivemind.queen.deps` (the Warden's link), `hivemind.queen.swarm_gate.colonize.errors`,
    `hivemind.swarm` and waggle only.

Key invariants:
    - Nothing here awaits except `ask`, and `ask` waits at most the order's deadline plus a margin.
    - A frame is consumed only when it is a `swarm.nuc_promoted`; a capacity report is observed
      and still goes on to the Queen.
    - Whatever waits on a device's Warden stops waiting the moment its connection ends.
    - A waiting order is forgotten however `ask` ends, so a late reply finds nobody and is dropped.

See Also:
    - hivemind.wardens.ticks.nuc for the Warden's side, which sends the reply.
    - hivemind.queen.swarm_gate.attach for the door that feeds `heard`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from hivemind.common.logging import get_logger
from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.swarm import SwarmNode
from waggle.clock import Clock
from waggle.envelope import Envelope, wrap
from waggle.ids import DeviceId, MessageId
from waggle.messages.forage import CapacityReport
from waggle.messages.swarm import NucPromote, NucPromoted

# Past an order's own deadline: the Warden answers FAILED at it, not before, and the answer then
# has to cross the link.
REPLY_MARGIN_S = 15.0

log = get_logger(__name__)

__all__ = ["REPLY_MARGIN_S", "NucDesk"]


@dataclass(slots=True)
class _Waiting:
    """One order in flight: whose it was, and the future its answer resolves."""

    device_id: DeviceId
    answer: asyncio.Future[NucPromoted]


class NucDesk:
    """Keep what device Wardens have declared, and carry the orders sent to them."""

    def __init__(self) -> None:
        """Build an empty desk: no device has declared anything and no order is in flight."""
        self._declared: dict[DeviceId, tuple[str, ...]] = {}
        self._waiting: dict[MessageId, _Waiting] = {}

    def declared(self, device_id: DeviceId) -> tuple[str, ...]:
        """Return the server ids `device_id`'s Warden last declared, or none it has not told of."""
        return self._declared.get(device_id, ())

    def heard(self, node: SwarmNode, envelope: Envelope) -> bool:
        """Take in one frame of `node`'s Warden; True when it was the gate's and is now consumed.

        Args:
            node: The device whose Warden sent the frame.
            envelope: The verified frame.

        Returns:
            True for a `swarm.nuc_promoted` (the gate's, never the Queen's); False for everything
            else, including a capacity report, whose servers are noted on the way past.
        """
        payload = envelope.payload
        if isinstance(payload, CapacityReport):
            # A Warden speaks only for its own Cell; a report that names another is not noted.
            if payload.cell_id == node.cell_id:
                self._declared[node.device_id] = tuple(s.server_id for s in payload.model_servers)
            return False
        if isinstance(payload, NucPromoted):
            self._answer(node, envelope.correlation_id, payload)
            return True
        return False

    def lost(self, device_id: DeviceId) -> None:
        """End every wait on `device_id`'s Warden: its connection is gone and cannot answer."""
        for waiting in self._waiting.values():
            if waiting.device_id == device_id and not waiting.answer.done():
                waiting.answer.set_exception(ColonizeStepError("link_lost"))

    def forget(self, device_id: DeviceId) -> None:
        """Forget what `device_id` declared (it was revoked or withdrawn)."""
        self._declared.pop(device_id, None)

    async def ask(self, warden: WardenLink, order: NucPromote, clock: Clock) -> NucPromoted:
        """Send `order` to the Warden and wait for its answer.

        Args:
            warden: The Queen's link to the device's own Warden.
            order: The `swarm.nuc_promote` to send; its `deadline_s` bounds the wait.
            clock: Mints the order's envelope id and time.

        Returns:
            The Warden's `swarm.nuc_promoted`, whatever outcome it reports.

        Raises:
            ColonizeStepError: The link was closed (`link_lost`) or the Warden did not answer in
                time (`warden_silent`).
        """
        envelope = wrap(order, warden.hop, clock=clock)
        answer: asyncio.Future[NucPromoted] = asyncio.get_running_loop().create_future()
        # Registered before the send: a Warden on the same machine can answer before send returns.
        self._waiting[envelope.id] = _Waiting(order.device_id, answer)
        try:
            if not await warden.send(envelope):
                raise ColonizeStepError("link_lost")
            # The Warden's own answer; it replies FAILED at the deadline, and the margin is the
            # time that reply takes to arrive. On timeout the order is given up on.
            async with asyncio.timeout(order.deadline_s + REPLY_MARGIN_S):
                return await answer
        except TimeoutError as error:
            raise ColonizeStepError("warden_silent") from error
        finally:
            self._waiting.pop(envelope.id, None)

    def _answer(self, node: SwarmNode, to: MessageId | None, reply: NucPromoted) -> None:
        """Hand `reply` to the order it answers, when it is this device's and still waits."""
        waiting = self._waiting.get(to) if to is not None else None
        ours = waiting is not None and waiting.device_id == node.device_id
        if waiting is None or not ours or reply.cell_id != node.cell_id or waiting.answer.done():
            # Late (the order was given up on), or for another device's order: nobody wants it.
            log.warning("swarm.nuc_reply_dropped", device_id=node.device_id)
            return
        waiting.answer.set_result(reply)
