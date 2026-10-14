"""Tests for hivemind.queen.attach.serve: everything after a Warden's handshake, in one helper.

Both gates (the Cell listener and, from phase 11, the swarm gate) hand `serve_warden` what their
handshake proved and a frame iterator, so these tests script the connection instead of dialling a
socket: the frames the test pushes are what the Queen's link reads, and an error it pushes is what
ends the link. They cover the contract the gates rely on: a new Warden is attached as new and a
known one as a reconnection (the gate is told which); the end of a link detaches the Warden by
default, or calls the gate's own step and keeps the entry; a link that ends after its replacement
attached takes nothing away; a refused spawn leaves nothing running; a forged frame ends the link
and is recorded against its Cell; a snapshot relay request is answered on its own connection; and a
trail segment goes through the link's refusals.

Fits into the Hive:
    Mirrors src/hivemind/queen/attach/serve.py (codingrules section 3); the Cell listener's tests
    drive the same path over a real loopback WebSocket.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.attach.serve for the module under test.
    - tests.unit.queen.cell_gate.test_listener for the Cell gate over a real socket.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from builders.cells import make_capabilities
from builders.queen import make_queen_deps, with_guard_policy
from builders.segments import SegmentRig, segment_chunks

from hivemind.cell import Cell
from hivemind.guard import GuardPolicy, load_guard_policy
from hivemind.manifest import GuardRoleSection, GuardSection
from hivemind.pheromone import TrailQuery, TrailRecorder
from hivemind.queen import WardenSpawnRefusedError
from hivemind.queen.attach import LinkHooks, LinkServices, ProvedLink, serve_warden
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.queen import Queen
from hivemind.queen.trail import TrailSegmentReceiver
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import InvalidSignatureError
from waggle.ids import NodeId, WardenId, new_node_id, new_warden_id
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)
from waggle.messages.cell.status import CellReady
from waggle.messages.labels import AccessLevel as WireAccessLevel
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.transport.memory import MemoryTransport

WAIT_S = 5.0  # Bounds every await that could hang; an in-process connection answers at once.


class _Wire:
    """A scripted connection: what the test pushes is what the Queen's link reads."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Envelope | Exception | None] = asyncio.Queue()

    def push(self, envelope: Envelope) -> None:
        """Deliver one frame to the link."""
        self._queue.put_nowait(envelope)

    def end(self) -> None:
        """Close the connection cleanly: the iterator is exhausted."""
        self._queue.put_nowait(None)

    def fail(self, error: Exception) -> None:
        """End the connection on `error`, as the codec does for a frame that cannot be read."""
        self._queue.put_nowait(error)

    async def frames(self) -> AsyncIterator[Envelope]:
        """The one iterator of the connection's frames."""
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item


class _Spy:
    """The gate's two hooks, keeping what they were told and waking waiters."""

    def __init__(self) -> None:
        self.attached: list[tuple[WardenLink, bool]] = []
        self.ended: list[WardenLink] = []
        self._attached = asyncio.Event()

    async def on_attached(self, link: WardenLink, reconnected: bool) -> None:
        self.attached.append((link, reconnected))
        self._attached.set()

    async def on_ended(self, link: WardenLink) -> None:
        self.ended.append(link)

    def hooks(self, *, keeps_entry: bool = False) -> LinkHooks:
        """Hooks that note the attach, and, when `keeps_entry`, the end too."""
        return LinkHooks(
            on_attached=self.on_attached, on_ended=self.on_ended if keeps_entry else None
        )

    async def attach_seen(self) -> None:
        """Wait until the Queen has attached the Warden."""
        await asyncio.wait_for(self._attached.wait(), WAIT_S)


@dataclass(slots=True)
class _Rig:
    """One Queen, one remote Warden's identity, and the services her links are served with."""

    clock: FakeClock
    queen: Queen
    deps: QueenDeps
    cell: Cell
    warden_id: WardenId
    node_id: NodeId
    services: LinkServices
    served: list[asyncio.Task[None]] = field(default_factory=list)


@dataclass(slots=True)
class _Conn:
    """One scripted connection: its wire, the Warden's end of its transport, its serve task."""

    wire: _Wire
    far: MemoryTransport
    proved: ProvedLink
    task: asyncio.Task[None]

    async def finish(self) -> None:
        """End the connection and wait for the serve to return."""
        self.wire.end()
        await asyncio.wait_for(self.task, WAIT_S)


def _rig(*, policy: GuardPolicy | None = None, with_trail: bool = False) -> _Rig:
    """A Queen over fakes and the identity of a device Warden that will dial her.

    With `policy`, the Queen's Guard decides against it; with `with_trail`, her links merge the
    trail segments their Wardens ship.
    """
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    if policy is not None:
        deps = with_guard_policy(deps, policy)
    recorder = TrailRecorder(deps.trail, clock, deps.identity.hive_id, new_node_id(clock))
    services = LinkServices(clock=clock, recorder=recorder)
    if with_trail:
        services.trail = TrailSegmentReceiver(deps.trail)
    return _Rig(
        clock,
        Queen(deps),
        deps,
        link.cell,
        new_warden_id(clock),
        new_node_id(clock),
        services,
    )


def _wardens(rig: _Rig) -> list[WardenLink]:
    """The links the Queen holds, as a list (a call, so no earlier assertion narrows it)."""
    return list(rig.queen.wardens)


def _ready(rig: _Rig) -> CellReady:
    """The `cell.ready` the rig's Warden announced itself with."""
    platform, capabilities = make_capabilities().to_wire()
    return CellReady(
        cell_id=rig.cell.id,
        warden_id=rig.warden_id,
        platform=platform,
        capabilities=capabilities,
        access_level=WireAccessLevel.FULL,
        comb_shield=WireCombShieldLevel.MEADOW,
        attestation=(),
        runtime_version="1.0.0",
    )


def _proved(rig: _Rig) -> tuple[ProvedLink, _Wire, MemoryTransport]:
    """What a gate's handshake proves for one new connection, its wire and the Warden's end."""
    clock, hive_id = rig.deps.clock, rig.deps.identity.hive_id
    queen_end, far = MemoryTransport.pair(Codec(), Codec())
    warden_hop = Hop(sender=rig.warden_id, recipient=hive_id, node_id=rig.node_id)
    wire = _Wire()
    proved = ProvedLink(
        cell=rig.cell,
        node_id=rig.node_id,
        first=wrap(_ready(rig), warden_hop, clock=clock),
        transport=queen_end,
        frames=wire.frames(),
        hop=Hop(sender=hive_id, recipient=rig.warden_id, node_id=rig.deps.identity.node_id),
    )
    return proved, wire, far


async def _connect(rig: _Rig, hooks: LinkHooks | None = None) -> _Conn:
    """Start serving a new connection of the rig's Warden."""
    proved, wire, far = _proved(rig)
    task = asyncio.create_task(serve_warden(rig.queen, proved, rig.services, hooks))
    rig.served.append(task)
    return _Conn(wire, far, proved, task)


async def _attached(rig: _Rig, hooks: LinkHooks | None = None) -> tuple[_Conn, _Spy]:
    """Connect and wait until the Queen has attached the Warden."""
    spy = _Spy()
    conn = await _connect(rig, hooks if hooks is not None else spy.hooks())
    await spy.attach_seen()
    return conn, spy


@pytest.fixture
async def rig() -> AsyncIterator[_Rig]:
    """A rig whose still-running serve tasks are ended and awaited when the test is done."""
    active = _rig()
    yield active
    for task in active.served:
        if not task.done():
            task.cancel()
    await asyncio.gather(*active.served, return_exceptions=True)


async def _kinds(rig: _Rig, kind: str) -> list[str]:
    """The ids of the Queen's trail events of `kind` about this rig's Warden, oldest first."""
    events = await rig.deps.trail.query(TrailQuery(kind=kind))
    return [e.subject_id for e in events if e.subject_id == rig.warden_id]


async def test_a_new_warden_is_attached_with_the_node_its_handshake_proved(rig: _Rig) -> None:
    conn, spy = await _attached(rig)

    [(link, reconnected)] = spy.attached
    assert reconnected is False
    assert (link.warden_id, link.cell, link.node_id) == (rig.warden_id, rig.cell, rig.node_id)
    assert link in _wardens(rig)
    assert await _kinds(rig, "warden.spawned") == [rig.warden_id]
    await conn.finish()


async def test_a_warden_that_dials_again_while_its_old_link_is_up_is_a_reconnection(
    rig: _Rig,
) -> None:
    first, _spy = await _attached(rig)

    second, spy = await _attached(rig)

    [(link, reconnected)] = spy.attached
    assert reconnected is True
    assert _wardens(rig) == [link]  # One link at a time: the new one replaced the old.
    assert await _kinds(rig, "warden.reconnected") == [rig.warden_id]
    await first.finish()
    await second.finish()


async def test_a_warden_that_dials_again_after_it_was_detached_is_a_reconnection(rig: _Rig) -> None:
    first, _spy = await _attached(rig)
    await first.finish()
    assert _wardens(rig) == []

    second, spy = await _attached(rig)

    assert [reconnected for _link, reconnected in spy.attached] == [True]
    assert await _kinds(rig, "warden.spawned") == [rig.warden_id]  # Only the first dial spawned.
    await second.finish()


async def test_by_default_the_end_of_the_connection_detaches_the_warden(rig: _Rig) -> None:
    conn, _spy = await _attached(rig)
    assert len(_wardens(rig)) == 1

    await conn.finish()

    assert _wardens(rig) == []


async def test_a_gate_that_keeps_the_entry_is_told_and_the_warden_stays_attached(
    rig: _Rig,
) -> None:
    spy = _Spy()
    conn = await _connect(rig, spy.hooks(keeps_entry=True))
    await spy.attach_seen()

    await conn.finish()

    [link] = spy.ended
    assert spy.attached[0][0] is link
    assert _wardens(rig) == [link]  # Held for its return, not forgotten.


async def test_a_connection_that_ends_after_its_replacement_attached_takes_nothing_away(
    rig: _Rig,
) -> None:
    old_spy, new_spy = _Spy(), _Spy()
    old = await _connect(rig, old_spy.hooks(keeps_entry=True))
    await old_spy.attach_seen()
    new = await _connect(rig, new_spy.hooks(keeps_entry=True))
    await new_spy.attach_seen()

    await old.finish()  # The old connection is found dead only now.

    assert old_spy.ended == []  # Not its Warden's current link any more.
    assert _wardens(rig) == [new_spy.attached[0][0]]
    await new.finish()
    assert new_spy.ended == [new_spy.attached[0][0]]


async def test_a_late_ending_old_connection_does_not_detach_the_replacement_by_default(
    rig: _Rig,
) -> None:
    old, _spy = await _attached(rig)
    new, spy = await _attached(rig)

    await old.finish()

    assert _wardens(rig) == [spy.attached[0][0]]
    await new.finish()
    assert _wardens(rig) == []


async def test_a_refused_spawn_is_raised_with_nothing_attached_and_no_task_left_running() -> None:
    section = GuardSection(roles={"queen": GuardRoleSection(allow=("forage:request",))})
    refused = _rig(policy=load_guard_policy(None, section))
    proved, _wire, _far = _proved(refused)
    before = asyncio.all_tasks()

    with pytest.raises(WardenSpawnRefusedError):
        await serve_warden(refused.queen, proved, refused.services)

    assert _wardens(refused) == []
    assert asyncio.all_tasks() == before  # The fan-out's pump was reaped.


async def test_a_forged_frame_ends_the_link_and_is_recorded_against_its_cell(rig: _Rig) -> None:
    conn, _spy = await _attached(rig)

    conn.wire.fail(InvalidSignatureError("A frame failed its signature."))
    await asyncio.wait_for(conn.task, WAIT_S)

    [refusal] = await rig.deps.trail.query(TrailQuery(kind="guard.envelope_refused"))
    assert refusal.subject_id == rig.cell.id
    assert refusal.payload["reason"] == "invalid"
    assert _wardens(rig) == []


class _Snapshots:
    """A snapshot handler stand-in: this module tests the relay's routing, not a backend."""

    async def snapshot(self, request: CellSnapshotRequest) -> CellSnapshotReply:
        return CellSnapshotReply(cell_id=request.cell_id, snapshot_id="snap_test", error=None)

    async def rollback(self, request: CellRollbackRequest) -> CellRollbackReply:
        return CellRollbackReply(cell_id=request.cell_id, ok=True, error=None)


async def _reply_to(conn: _Conn, kind: type) -> Envelope:
    """The first envelope of `kind` the Queen sends the Warden on this connection."""

    async def first() -> Envelope:
        async for envelope in conn.far.receive():
            if isinstance(envelope.payload, kind):
                return envelope
        raise AssertionError(f"The connection closed without a {kind.__name__}.")

    return await asyncio.wait_for(first(), WAIT_S)


async def test_a_snapshot_request_is_answered_on_its_own_connection_correlated_to_it(
    rig: _Rig,
) -> None:
    rig.services.snapshots = _Snapshots()
    conn, _spy = await _attached(rig)
    warden_hop = Hop(sender=rig.warden_id, recipient=rig.deps.identity.hive_id, node_id=rig.node_id)
    request = wrap(
        CellSnapshotRequest(cell_id=rig.cell.id, purpose="test"), warden_hop, clock=rig.deps.clock
    )

    conn.wire.push(request)
    reply = await _reply_to(conn, CellSnapshotReply)

    assert reply.correlation_id == request.id
    assert isinstance(reply.payload, CellSnapshotReply)
    assert reply.payload.snapshot_id == "snap_test"
    await conn.finish()


async def test_a_rollback_request_is_answered_the_same_way(rig: _Rig) -> None:
    rig.services.snapshots = _Snapshots()
    conn, _spy = await _attached(rig)
    warden_hop = Hop(sender=rig.warden_id, recipient=rig.deps.identity.hive_id, node_id=rig.node_id)
    request = wrap(
        CellRollbackRequest(cell_id=rig.cell.id, snapshot_id="snap_test"),
        warden_hop,
        clock=rig.deps.clock,
    )

    conn.wire.push(request)
    reply = await _reply_to(conn, CellRollbackReply)

    assert reply.correlation_id == request.id
    await conn.finish()


async def test_a_trail_segment_of_the_proved_node_is_merged_and_another_nodes_is_refused() -> None:
    merging = _rig(with_trail=True)
    conn, _spy = await _attached(merging)
    segment = SegmentRig(
        merging.clock,
        merging.deps.identity.hive_id,
        merging.cell.id,
        merging.node_id,
        merging.warden_id,
    )
    hop = Hop(sender=merging.warden_id, recipient=segment.hive_id, node_id=merging.node_id)
    [chunk] = await segment_chunks(segment)
    stranger = chunk.model_copy(update={"node_id": new_node_id(merging.deps.clock)})

    conn.wire.push(wrap(chunk, hop, clock=segment.clock))
    conn.wire.push(wrap(stranger, hop, clock=segment.clock))
    await conn.finish()

    assert len(await merging.deps.trail.query(TrailQuery(kind="cell.provisioned"))) == 1
    [refusal] = await merging.deps.trail.query(TrailQuery(kind="guard.segment_refused"))
    assert refusal.payload["reason"] == "another_node"
    assert refusal.subject_id == merging.cell.id


async def test_a_request_with_no_bound_handler_is_ignored_and_the_link_stays_up(rig: _Rig) -> None:
    conn, _spy = await _attached(rig)  # No snapshot handler and no trail receiver are bound.
    warden_hop = Hop(sender=rig.warden_id, recipient=rig.deps.identity.hive_id, node_id=rig.node_id)

    conn.wire.push(
        wrap(
            CellSnapshotRequest(cell_id=rig.cell.id, purpose="x"), warden_hop, clock=rig.deps.clock
        )
    )
    await asyncio.sleep(0)  # Let the pump take it.

    assert not conn.task.done()
    assert len(_wardens(rig)) == 1
    await conn.finish()
