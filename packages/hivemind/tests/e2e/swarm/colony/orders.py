"""Listen at the Queen's door for what a device Warden says, and speak to it as the Queen would.

A colonized device's Warden dials the Hive Stand itself, and the Queen's gate is the one place every
frame of that connection passes (`DoorEvents.heard`) and the one place she sends a Nuc order from
(`NucDesk.ask`). The scenario needs both sides of that conversation as it happened, not only its
effects on the trail: how many `task.result` frames a task's Warden sent (one, however many times
the link broke), that a `forage.request` came after the link came back and not before, and what
the `swarm.nuc_promote` the Queen sent said and what the Warden answered. `tap` wraps those two
calls on the running Hive so that they record what passes and change nothing, and `order` sends the
Warden an order of the scenario's own through the same desk, which is how the scenario asks it to
load a model the Hive allows and one it does not.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the phase 11 Nuc scenario;
    reaches into the Hive's swarm gate, which has no public way to listen at its door.

Key invariants:
    - A tap only records: the frame it heard is returned to the gate as the gate would have had
      it, and an order is answered as it would have been.
    - Frames are kept by kind and task, never by content: a result's text, a request's reason and
      an order's words stay where they were.
    - An order sent by `order` goes over the Warden's own link and waits for its answer, bounded by
      the order's own deadline, exactly as one the Queen sends.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.desk for the desk whose `ask` is wrapped.
    - hivemind.queen.swarm_gate.attach for the door whose frames are heard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from hivemind.cli.compose import Hive
from hivemind.queen.deps import WardenLink
from hivemind.swarm import SwarmNode
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.messages.swarm import NucAction, NucPromote, NucPromoted

ORDER_DEADLINE_S = 60.0  # A model server starts, or an order is refused, well within this.

__all__ = ["ORDER_DEADLINE_S", "Ask", "Frame", "Tap", "order", "tap"]


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame of a device Warden's connection: its kind, the task it names, and when.

    Attributes:
        kind: The frame's wire kind.
        task_id: The task it names, when it names one.
        sent_at: When the Warden stamped it. A frame the outbox held is sent in a fresh envelope or
            under the stamp it was queued with, so this says when it was made, not when it arrived.
        heard_at: When the Queen's door heard it: the Hive's own clock, so that "arrived after the
            link came back" is a comparison with a moment the scenario noted.
        envelope_id: The envelope's id, which is how a frame the Warden sent again is told from a
            second frame of the same words.
    """

    kind: str
    task_id: str | None
    sent_at: datetime
    heard_at: datetime
    envelope_id: str = ""


@dataclass(frozen=True, slots=True)
class Ask:
    """One order the Queen sent a Warden, and its answer, with the moments she sent and had it."""

    order: NucPromote
    reply: NucPromoted
    asked_at: datetime
    answered_at: datetime


@dataclass(slots=True)
class Tap:
    """What a device Warden said at the Queen's door, and the Nuc orders she sent it."""

    frames: list[Frame] = field(default_factory=list)
    orders: list[Ask] = field(default_factory=list)

    def of(self, kind: str, task_id: str | None = None) -> list[Frame]:
        """The frames of `kind` (about `task_id`, when given), in the order they were heard."""
        return [
            frame
            for frame in self.frames
            if frame.kind == kind and (task_id is None or frame.task_id == task_id)
        ]


def tap(hive: Hive) -> Tap:
    """Start recording every frame the Queen's door hears from a device Warden, and her orders.

    Args:
        hive: The Hive, built and started, whose swarm gate is listened at.

    Returns:
        The record, which fills as the scenario runs.
    """
    assert hive.swarm is not None
    colonizer = hive.swarm.gate._colonizer
    desk = colonizer._desk
    record = Tap()
    heard, ask = colonizer.heard, desk.ask

    def hear(node: SwarmNode, envelope: Envelope) -> bool:
        task_id = getattr(envelope.payload, "task_id", None)
        now = hive.clock.now()
        record.frames.append(Frame(envelope.kind, task_id, envelope.sent_at, now, envelope.id))
        return heard(node, envelope)

    async def asking(warden: WardenLink, nuc_order: NucPromote, clock: Clock) -> NucPromoted:
        asked_at = clock.now()
        reply = await ask(warden, nuc_order, clock)
        record.orders.append(Ask(nuc_order, reply, asked_at, clock.now()))
        return reply

    colonizer.heard = hear  # type: ignore[method-assign]
    desk.ask = asking  # type: ignore[method-assign,assignment]
    return record


async def order(
    hive: Hive, node: SwarmNode, action: NucAction, server_id: str | None, models: tuple[str, ...]
) -> NucPromoted:
    """Send the device's Warden a `swarm.nuc_promote` of the scenario's own and wait for its answer.

    Args:
        hive: The running Hive.
        node: The Nuc.
        action: PROMOTE to start (or load into) a server, DEMOTE to stop every one.
        server_id: The server to start or load into; None for a DEMOTE.
        models: The Forage map sources to offer, for a PROMOTE.

    Returns:
        The Warden's `swarm.nuc_promoted`, whatever outcome it reports.
    """
    assert hive.swarm is not None
    link = hive.swarm.gate.wardens.link_of(node.warden_id)
    assert link is not None, "the device's Warden is not attached"
    nuc_order = NucPromote(
        cell_id=node.cell_id,
        device_id=node.device_id,
        action=action,
        server_id=server_id,
        initial_models=models,
        deadline_s=ORDER_DEADLINE_S,
        reason="An order of the scenario's own.",
    )
    desk = hive.swarm.gate._colonizer._desk
    return await desk.ask(link, nuc_order, hive.clock)
