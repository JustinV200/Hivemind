"""Admit, check and route every frame the Queen and the Warden send: the dispatcher.

The Pollen Packet (the thin gateway program on a device the Hive borrows) acts on a frame only if
it passes every rule of gateway-contract sections 6 and 7, and does nothing at all otherwise: the
frame is addressed to this device, its sender is the Queen (the Hive's central orchestrator) or the
Cell's Warden (the per-Cell supervisor), a kind only the Queen may send comes from the Queen, a
frame in the Queen's name is signed by a Hive node, it is neither stale (older than seven days)
nor from the future (more than five minutes ahead), and it is not one the replay window has seen.
An admitted frame from a Hive node is also what tells the dead-man switch the Queen is alive. The
payload's *type* (never its kind string) picks an executor from a table built from what each
executor says it ``handles``; the executor reads and resolves the request (``prepare``), the
request's needs are checked against the capabilities the node holds *now*, and only then does the
work run. A refusal from any step is a ``control.error`` correlated to the request; a request kind
nobody serves draws ``pollen.unsupported`` and an event kind nobody serves is dropped; a bug inside
the packet is reported as ``pollen.internal_error`` without a traceback and never ends the loop.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Built by the composition root
    with the executors; fed by pollen.agent.packet's reader; sends only through
    pollen.agent.courier; calls into pollen.agent.checks, pollen.agent.moved and waggle.

Key invariants:
    - Nothing is acted on until every admission rule holds, and a dropped frame is counted by its
      reason and never answered.
    - Capabilities are read from the state at the moment of the check, so a ``swarm.node_update``
      applies from the next request.
    - Exactly one answer path exists for a failure: ``Courier.fail``, with a code from the wire
      table; a local fault is reported as ``pollen.internal_error``.

See Also:
    - docs/waggle/gateway-contract.md sections 6, 7, 9.5, 10 and 15.
    - pollen.executors.base for the two-step DeviceExecutor the table is built from.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pollen.agent.checks import check_needs
from pollen.agent.courier import Courier
from pollen.agent.moved import hive_nodes
from pollen.errors import WIRE_CODES, InternalError, PollenError, UnsupportedError
from pollen.executors.base import DeviceExecutor
from pollen.state import PacketState, StateStore
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.errors import InvalidIdError, InvalidPayloadError
from waggle.ids import IdKind, MessageId, parse_id
from waggle.messages.base import (
    MAX_CLOCK_SKEW_S,
    MAX_MESSAGE_AGE_S,
    MessageShape,
    WaggleMessage,
)
from waggle.messages.control import ErrorMessage, QueenMoved
from waggle.messages.control.protocol import MAX_ERROR_MESSAGE_CHARS
from waggle.messages.registry import spec_for
from waggle.messages.swarm import Colonize, EnrolAccept, NodeUpdate
from waggle.transport.replay import ReplayWindow

log = logging.getLogger(__name__)

# The kinds only the Queen sends (gateway-contract section 6, rule 3); matched by payload type.
QUEEN_ONLY_PAYLOADS: tuple[type[WaggleMessage], ...] = (
    EnrolAccept,
    QueenMoved,
    NodeUpdate,
    Colonize,
)
INVALID_PAYLOAD_CODE = "waggle.codec.invalid_payload"  # What a frame with a bad payload is told.

__all__ = ["QUEEN_ONLY_PAYLOADS", "DispatchDeps", "Dispatcher", "DropReason", "admit"]


class DropReason(Enum):
    """Why an arriving frame was dropped without acting."""

    MISADDRESSED = "MISADDRESSED"  # Its recipient is not this device.
    SENDER = "SENDER"  # Not from the Queen or the Warden, or the Queen's name on a foreign node.
    QUEEN_ONLY = "QUEEN_ONLY"  # A kind only the Queen sends, from the Warden.
    STALE = "STALE"  # Older than MAX_MESSAGE_AGE_S.
    FUTURE = "FUTURE"  # More than MAX_CLOCK_SKEW_S ahead of this device's clock.
    DUPLICATE = "DUPLICATE"  # The replay window has seen this id.


@dataclass(frozen=True, slots=True)
class DispatchDeps:
    """What the dispatcher needs from the packet, bundled so it takes few parameters."""

    state: StateStore  # Identity, senders and grants, read afresh for every frame.
    clock: Clock  # Judges freshness and stamps the replay window.
    courier: Courier  # How a refusal is sent.
    heard: Callable[[], None]  # Tells the dead-man switch the Queen was heard from.
    on_drop: Callable[[str], None]  # Counts a dropped frame by its reason.
    on_admit: Callable[[], None]  # Records that a frame passed every rule.


def admit(
    envelope: Envelope, state: PacketState, now: datetime, nodes: frozenset[str]
) -> DropReason | None:
    """Apply the sender and freshness rules to a verified frame.

    Args:
        envelope: A frame whose signature already verified.
        state: The persisted identity and pins.
        now: The device's current time.
        nodes: The nodes that may speak as the Queen right now.

    Returns:
        Why the frame must be dropped, or None if it may be acted on (the replay window is the
        caller's last check).
    """
    if envelope.recipient != state.device_id:
        return DropReason.MISADDRESSED
    from_queen = envelope.sender == state.hive_id
    if not from_queen and envelope.sender != state.warden_id:
        return DropReason.SENDER
    # The Queen's address on a frame signed by a node that is not a Hive node is a Warden's
    # node borrowing her name; no Queen-only kind or Queen-level trust may follow from that.
    if from_queen and envelope.node_id not in nodes:
        return DropReason.SENDER
    if not from_queen and isinstance(envelope.payload, QUEEN_ONLY_PAYLOADS):
        return DropReason.QUEEN_ONLY
    age_s = (now - envelope.sent_at).total_seconds()
    if age_s > MAX_MESSAGE_AGE_S:
        return DropReason.STALE
    if -age_s > MAX_CLOCK_SKEW_S:
        return DropReason.FUTURE
    return None


class Dispatcher:
    """Routes admitted frames to the executor that serves their payload type.

    Owns mutable state (codingrules 8.5, documented): the replay window of envelope ids seen,
    which only ``handle`` touches, one frame at a time on the reader's task.
    """

    def __init__(self, deps: DispatchDeps, executors: Iterable[DeviceExecutor]) -> None:
        """Build the payload-type table from what each executor handles.

        Args:
            deps: State, clock, courier and the packet's counters.
            executors: Every executor, the lease and control services included.

        Raises:
            ValueError: Two executors claim the same payload type.
        """
        self._deps = deps
        self._replay = ReplayWindow()
        self._handlers: dict[type[WaggleMessage], DeviceExecutor] = {}
        for executor in executors:
            for payload_type in executor.handles:
                if payload_type in self._handlers:
                    raise ValueError(f"Two executors handle {payload_type.__name__}.")
                self._handlers[payload_type] = executor

    async def handle(self, envelope: Envelope) -> None:
        """Admit, check and carry out one verified frame; never raises on a bad request.

        Args:
            envelope: A frame the link verified.
        """
        state = self._deps.state.current
        now = self._deps.clock.now()
        nodes = hive_nodes(state, now)
        reason = admit(envelope, state, now, nodes)
        # The replay window is asked last, so a frame dropped by a rule above is never remembered
        # and a later valid frame with the same id still gets its chance.
        if reason is None and self._replay.seen(envelope.node_id, envelope.id, now):
            reason = DropReason.DUPLICATE
        if reason is not None:
            self._deps.on_drop(reason.value)
            log.debug("pollen.frame_dropped reason=%s kind=%s", reason.value, envelope.kind)
            return
        self._deps.on_admit()
        if envelope.node_id in nodes:
            self._deps.heard()  # A fresh frame from a Hive node: the Queen is alive.
        await self._route(envelope, state)

    async def refuse_invalid(self, error: InvalidPayloadError) -> None:
        """Answer a frame whose id was readable but whose payload or envelope was not valid.

        Args:
            error: The codec's refusal; it carries the raw id, sender and kind of the frame.
        """
        state = self._deps.state.current
        # Only the Queen and the Warden are ever answered; anyone else's bad frame is ignored.
        if error.sender not in (state.hive_id, state.warden_id) or error.message_id is None:
            return
        try:
            correlation = MessageId(parse_id(error.message_id, IdKind.MESSAGE))
        except InvalidIdError:
            return
        text = str(error)[:MAX_ERROR_MESSAGE_CHARS] or INVALID_PAYLOAD_CODE
        reply = ErrorMessage(
            code=INVALID_PAYLOAD_CODE, message=text, failed_kind=error.kind, is_retryable=False
        )
        await self._deps.courier.send(reply, to=error.sender, correlation_id=correlation)

    async def _route(self, envelope: Envelope, state: PacketState) -> None:
        """Find the executor, check its request's needs, run it, and answer a failure."""
        executor = self._handlers.get(type(envelope.payload))
        if executor is None:
            await self._unserved(envelope)
            return
        try:
            prepared = executor.prepare(envelope)
            check_needs(
                state.granted_capabilities, prepared.needs, prepared.scratch_root, envelope.id
            )
            await prepared.run()
        except PollenError as error:
            await self._deps.courier.fail(envelope, _as_wire(error, envelope))
        except (
            Exception
        ) as error:  # SAFETY: the top of the frame loop; a packet bug must not end it.
            log.error("pollen.handler_failed kind=%s error=%s", envelope.kind, type(error).__name__)
            await self._deps.courier.fail(envelope, _internal(envelope))

    async def _unserved(self, envelope: Envelope) -> None:
        """Answer a request kind nobody serves with ``pollen.unsupported``; drop an event."""
        if spec_for(envelope.kind).shape is MessageShape.REQUEST:
            error = UnsupportedError(f"Kind {envelope.kind} is not implemented by this gateway.")
            await self._deps.courier.fail(envelope, error)
        else:
            log.debug("pollen.event_dropped kind=%s", envelope.kind)


def _as_wire(error: PollenError, envelope: Envelope) -> PollenError:
    """Return ``error`` if its code is on the wire table, else an internal error for the request."""
    if error.code in WIRE_CODES:
        return error
    log.error("pollen.local_fault kind=%s code=%s", envelope.kind, error.code)
    return _internal(envelope)


def _internal(envelope: Envelope) -> InternalError:
    """Build the sentence an internal fault reports: the request's id and nothing else."""
    return InternalError(f"Request {envelope.id} could not be handled because of a fault here.")
