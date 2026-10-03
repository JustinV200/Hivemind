"""Define serve_warden: attach a remote Warden's proved connection to the Queen and serve it.

Two gates admit remote Wardens: the Cell listener (a Virtual Cell's in-Cell Warden dials it) and
the swarm gate (a colonized device's Warden dials the swarm listener, ADR-0052). Their handshakes
differ, because they prove who is calling differently, but everything after a handshake is one
thing, and this is it, so there is no second copy: the connection is wrapped in a `FanoutTransport`
(one reader, answering a snapshot relay request or merging a trail segment inline and queueing the
rest for the Queen), the Warden is attached to the Queen through her `warden_spawn` point
(`Queen.attach_warden`, which treats a Warden she already knows as a reconnection), the gate is told
it is attached, and then the link is served until its connection ends. What the gate has proved is
`ProvedLink`: the Cell, the node that signs for it, the verified `cell.ready`, the transport and the
one iterator of its frames. What the gate wants done when the link ends is its `LinkHooks`: a
Virtual Cell's lifecycle owns a dead container, so by default the Warden is detached at once; a
device is somebody's machine that comes back, so the swarm gate keeps the Warden's entry and holds
its tasks.

Either way the end of a link acts only if that link is still the Warden's current one. A Warden that
reconnected on a new connection before the old one was found dead has already had its link swapped,
and the old connection ending late must neither detach it nor start a hold.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `hivemind.queen.attach`.
    Called by `hivemind.queen.cell_gate.listener.CellListener` for each Virtual Cell connection and
    by `hivemind.queen.swarm_gate.attach` for each device Warden's. Calls into `hivemind.cell`
    (Cell), `hivemind.pheromone` (TrailRecorder), `hivemind.queen.attach` (its own modules),
    `hivemind.queen.deps` (WardenLink, send_guarded), `hivemind.queen.errors`,
    `hivemind.queen.trail` (the receiver) and waggle only.

Key invariants:
    - Every connection handed here is either attached (a `WardenLink` given to
      `Queen.attach_warden`) or left for the caller to close: a Warden the Queen's set refuses
      to spawn re-raises `WardenSpawnRefusedError` with the fan-out's pump already reaped.
    - The fan-out's pump is always reaped before this returns, whatever ended the link, and the
      frames iterator is read by it alone from the moment it is wrapped.
    - A forged frame on an attached link (a failed signature) is recorded as
      `guard.envelope_refused` against the link's Cell; one that fails before the link is attached
      proves no Cell and is the gate's to close, not recorded here.
    - A snapshot relay request is answered on the connection it arrived on, correlated to its own
      envelope; a trail segment chunk is handed to the receiver and answered only when refused,
      with a `control.error` naming the reason. Both are no-ops when the services name no handler
      or receiver.
    - A returning Warden's gate hears of it (`LinkHooks.on_returning`) before the fan-out's pump
      reads anything after the handshake, so a duplicate in its first frames is never met before
      the transport knows it is expected.

See Also:
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the Cell gate.
    - docs/adr/0052-runtime-ladder-warden-moves-first.md for a device Warden's connection.
    - hivemind.queen.attach.fanout for the one-reader wrapper, `.refusals` for what is recorded.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Protocol

from hivemind.cell import Cell
from hivemind.pheromone import TrailRecorder
from hivemind.queen.attach.admit import current_link, detach_if_current, knows_warden
from hivemind.queen.attach.fanout import FanoutTransport
from hivemind.queen.attach.refusals import LinkRefusals, refusal_error
from hivemind.queen.deps import LiveCapacity, WardenLink, send_guarded
from hivemind.queen.errors import WardenSpawnRefusedError
from hivemind.queen.trail import TrailSegmentReceiver
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import SignatureError
from waggle.ids import CellId, NodeId, WardenId
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)
from waggle.messages.cell.status import CellReady
from waggle.messages.swarm import TrailSegmentSync
from waggle.transport.base import Transport

if TYPE_CHECKING:
    from hivemind.queen.queen import Queen

__all__ = [
    "AnswerClaims",
    "LinkHooks",
    "LinkServices",
    "ProvedLink",
    "SnapshotRequestHandler",
    "serve_warden",
]


class SnapshotRequestHandler(Protocol):
    """Answers a CellSnapshotRequest/CellRollbackRequest a remote Warden's link carries.

    A structural Protocol, not a base class, so `hivemind.queen.cell_gate.snapshot.
    CellSnapshotHandler` (the one production implementation) and a test's own stand-in both
    satisfy it without either importing the other.
    """

    async def snapshot(self, request: CellSnapshotRequest) -> CellSnapshotReply:
        """Answer `request`; see `CellSnapshotHandler.snapshot`."""
        ...

    async def rollback(self, request: CellRollbackRequest) -> CellRollbackReply:
        """Answer `request`; see `CellSnapshotHandler.rollback`."""
        ...


class AnswerClaims(Protocol):
    """Takes a frame a Queen-side wait registered for, before the Queen's own reader sees it.

    A structural Protocol, like `SnapshotRequestHandler`: `hivemind.queen.cell_gate.scrub.
    ScrubAcks` (the Overwintering scrub's answers) is the one production implementation.
    """

    def claim(self, cell_id: CellId, envelope: Envelope) -> bool:
        """Take `envelope`, sent on `cell_id`'s own link, if a wait registered for it.

        Returns:
            True once taken, so the fan-out queues it for nobody else; False otherwise.
        """
        ...


@dataclass(slots=True)
class LinkServices:
    """What the Queen offers a remote Warden's link besides her tick: answers and a receiver.

    Owns its mutable state in place (codingrules section 8.5): a gate builds it with its clock and
    recorder and binds the snapshot handler and the trail receiver later, because the composition
    root's own handler and receiver need the gate to exist first (the same late binding the Cell
    listener has always had). Every link reads the current values on each frame, so a binding made
    after a link attached still reaches it.

    Attributes:
        clock: Stamps the replies the Queen sends over a link.
        recorder: Where a link's refusals are recorded; None records nothing, and refuses the same.
        snapshots: Answers snapshot relay requests; None leaves them unanswered.
        trail: Merges trail segment chunks; None leaves them unmerged.
        claims: Takes the frames a Queen-side wait registered for (`AnswerClaims`); None takes
            none.
    """

    clock: Clock
    recorder: TrailRecorder | None = None
    snapshots: SnapshotRequestHandler | None = None
    trail: TrailSegmentReceiver | None = None
    claims: AnswerClaims | None = None


@dataclass(frozen=True, slots=True)
class ProvedLink:
    """What a gate's handshake proved about one accepted connection, ready to be attached.

    Attributes:
        cell: The Cell the Warden supervises, as the gate knows it (a Virtual Cell's from its
            `cell.ready`, a device's from the registry).
        node_id: The node whose key signed the connection's frames: it speaks for the Cell.
        first: The verified `cell.ready` envelope that identified the Warden.
        transport: The accepted transport; sends and the close go through it.
        frames: The one iterator of the connection's frames, positioned after the handshake.
        hop: The Queen's address, the Warden's and the node the Queen signs as, stamped on every
            envelope the Queen sends this Warden.
        live_capacity: Reads the Cell's capacity as it stands now, for a Cell whose capacity is
            refreshed live (a device's, from its capacity reports); None for one fixed for its
            life (a Virtual Cell's).
    """

    cell: Cell
    node_id: NodeId
    first: Envelope
    transport: Transport
    frames: AsyncIterator[Envelope]
    hop: Hop
    live_capacity: LiveCapacity | None = None


@dataclass(frozen=True, slots=True)
class LinkHooks:
    """What a gate wants done at the two edges of a link; both optional.

    Attributes:
        on_returning: Called, with no await, for a Warden the Queen has had before, ahead of
            everything else: before the link's pump has read a frame beyond the handshake. A
            returning Warden sends again what it may have lost (ADR-0053), so the duplicates the
            transport's replay window meets in its first frames are expected; the gate says so
            here, and not in `on_attached`, which only runs after the Queen has finished the
            attach, by which time the pump has already met the first of them.
        on_attached: Awaited once the Queen has attached the Warden, before the link is served,
            with the link and whether the Warden was a reconnection. The Cell gate resolves its
            readiness here; the swarm gate tells the colonization that is waiting for the
            device's Warden.
        on_ended: Awaited when the link's connection ends and it is still the Warden's current
            one. None detaches the Warden from the Queen (a Virtual Cell's lifecycle owns a dead
            container); a gate that keeps the entry for a returning Warden supplies its own.
    """

    on_returning: Callable[[], None] | None = None
    on_attached: Callable[[WardenLink, bool], Awaitable[None]] | None = None
    on_ended: Callable[[WardenLink], Awaitable[None]] | None = None


async def serve_warden(
    queen: Queen, proved: ProvedLink, services: LinkServices, hooks: LinkHooks | None = None
) -> None:
    """Attach a proved connection's Warden to `queen`, serve its link, and end it properly.

    Args:
        queen: The Queen the Warden is attached to, and detached from.
        proved: What the gate's handshake proved about the connection.
        services: What the Queen answers and merges on this link's behalf.
        hooks: The gate's own steps at attach and at the link's end; none by default.

    Raises:
        WardenSpawnRefusedError: The Queen's set refuses `warden:spawn` (the refusal is already on
            her trail). Nothing was attached and the pump is reaped; the caller closes the
            connection.
    """
    hooks = hooks if hooks is not None else LinkHooks()
    ready = proved.first.payload
    assert isinstance(ready, CellReady)  # noqa: S101 - the gate verified the handshake frame.
    refusals = LinkRefusals(services.recorder, proved.cell.id, proved.node_id)
    answers = _Answers(services, proved, refusals)
    _expect_replay(queen, ready.warden_id, hooks)  # Before the fan-out: its pump starts reading.
    # The Cell the handshake proved, never one a frame names: only its own answer counts.
    fanout = FanoutTransport(
        proved.transport, answers, proved.frames, partial(_claimed, services, proved.cell.id)
    )
    link = WardenLink(
        warden_id=ready.warden_id,
        cell=proved.cell,
        transport=fanout,
        hop=proved.hop,
        live_capacity=proved.live_capacity,
        node_id=proved.node_id,  # Proved by the handshake: the node that speaks for the Cell.
    )
    try:
        reconnected = await queen.attach_warden(link)
    except WardenSpawnRefusedError:
        # The pump started at construction; reap it so no task outlives the refusal.
        await _reap(fanout)
        raise
    try:
        if hooks.on_attached is not None:
            await hooks.on_attached(link, reconnected)
        # Ends normally once the pump notices the connection close (or a decode failure); it never
        # raises on that path, so the only exception that can reach here is a genuine external
        # cancel (a listener stopping).
        await fanout.pump_task
        if isinstance(fanout.closed_by, SignatureError):
            # A forged frame on a proved link (roadmap step 10.6): the Guard Bee hears of it.
            await refusals.envelope(fanout.closed_by)
    finally:
        await _reap(fanout)
        await _end(queen, link, hooks)


def _expect_replay(queen: Queen, warden_id: WardenId, hooks: LinkHooks) -> None:
    """Tell the gate a returning Warden is on the line, before anything is read from it.

    The first frames behind a returning Warden's handshake are the ones it sends again (ADR-0053),
    and the replay window drops those that arrived the first time. They are expected, and the
    transport has to know it before it meets the first, which is when the fan-out's pump starts.
    """
    if hooks.on_returning is not None and knows_warden(queen, warden_id):
        hooks.on_returning()


async def _end(queen: Queen, link: WardenLink, hooks: LinkHooks) -> None:
    """Carry out the gate's end-of-link step, or detach the Warden, if `link` is still current."""
    if hooks.on_ended is not None:
        # The gate decides what a lost link means; it is told only for the Warden's current link.
        if current_link(queen, link.warden_id) is link:
            await hooks.on_ended(link)
        return
    await detach_if_current(queen, link)


async def _reap(fanout: FanoutTransport) -> None:
    """Cancel the fan-out's pump if it is still running, and wait for it to end."""
    if not fanout.pump_task.done():
        fanout.pump_task.cancel()
    # gather, not a bare await: it collects the cancelled pump without swallowing a cancellation
    # of the task that called this.
    await asyncio.gather(fanout.pump_task, return_exceptions=True)


class _Answers:
    """Answer the frames of one link that are the gate's, not the Queen's: relays and segments."""

    def __init__(self, services: LinkServices, proved: ProvedLink, refusals: LinkRefusals) -> None:
        """Bind the answers to one link's services, proof and refusals."""
        self._services = services
        self._proved = proved
        self._refusals = refusals
        # By payload type, never a kind string: a frame of any other type has no entry and is the
        # Queen's, which the fan-out never hands here.
        self._by_payload: dict[type[WaggleMessage], Callable[[Envelope], Awaitable[None]]] = {
            CellSnapshotRequest: self._snapshot,
            CellRollbackRequest: self._rollback,
            TrailSegmentSync: self._segment,
        }

    async def __call__(self, envelope: Envelope) -> None:
        """Answer `envelope` if its kind has an answer; else do nothing."""
        handler = self._by_payload.get(type(envelope.payload))
        if handler is not None:
            await handler(envelope)

    async def _snapshot(self, envelope: Envelope) -> None:
        """Answer a snapshot relay request through the bound handler, if there is one."""
        handler, request = self._services.snapshots, envelope.payload
        if handler is not None and isinstance(request, CellSnapshotRequest):
            await self._reply(envelope, await handler.snapshot(request))

    async def _rollback(self, envelope: Envelope) -> None:
        """Answer a rollback request through the bound handler, if there is one."""
        handler, request = self._services.snapshots, envelope.payload
        if handler is not None and isinstance(request, CellRollbackRequest):
            await self._reply(envelope, await handler.rollback(request))

    async def _segment(self, envelope: Envelope) -> None:
        """Merge a trail segment chunk through the link's refusals, if a receiver is bound."""
        receiver, chunk = self._services.trail, envelope.payload
        if receiver is not None and isinstance(chunk, TrailSegmentSync):
            refusal = await self._refusals.merge(receiver, envelope, chunk)
            if refusal is not None:
                # A refused chunk's Warden is told it was not merged, and why (spec 8.10).
                await self._reply(envelope, refusal_error(refusal, chunk))

    async def _reply(self, request: Envelope, reply: WaggleMessage) -> None:
        """Send `reply` back on this link, correlated to the request that asked for it."""
        # A reply the Warden never receives is the same as one it never asked for: its own
        # RelaySnapshotter times out and falls back to REVERSE_DIFF (ADR-0018) either way.
        envelope = wrap(
            reply, self._proved.hop, clock=self._services.clock, correlation_id=request.id
        )
        await send_guarded(self._proved.transport, envelope)


def _claimed(services: LinkServices, cell_id: CellId, envelope: Envelope) -> bool:
    """Whether a Queen-side wait took `envelope`, sent on `cell_id`'s link (`AnswerClaims`)."""
    # Read on every frame, so claims bound after the link attached still reach it.
    claims = services.claims
    return claims is not None and claims.claim(cell_id, envelope)
