"""Tests for hivemind.wardens.snapshot_relay: RelaySnapshotter, the Warden-side snapshot relay.

Fits into the Hive:
    Mirrors src/hivemind/wardens/snapshot_relay.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

A snapshot freezes the whole Cell, its Warden included, so the relay announces the freeze before
each request (a Heartbeat declaring its own timeout as the Warden's interval): the Queen then never
reads the Hive's own freeze as the Warden gone silent. A reply resolves only the ask whose request
its envelope names in `correlation_id` (phase 5 open item 7), so every reply below is handed over
with the id of the request envelope it answers, as the Warden's tick hands it over.

See Also:
    - hivemind.wardens.snapshot_relay for the module under test.
    - packages/hivemind/tests/unit/wardens/ticks/test_control.py for the end-to-end round trip
      through handle_snapshot_reply, the Warden's own tick-side dispatch.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest
from builders.cells import make_cell
from builders.wardens import make_warden_deps

from hivemind.cell import CellKind, SnapshotId, SnapshotUnsupportedError
from hivemind.wardens.snapshot_relay import RelaySnapshotter
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop
from waggle.ids import new_cell_id, new_hive_id, new_node_id, new_warden_id
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)
from waggle.transport.memory import MemoryTransport


def _build_relay(
    clock: FakeClock, *, timeout_s: float = 5.0
) -> tuple[RelaySnapshotter, MemoryTransport]:
    """Build a RelaySnapshotter over one half of a fresh MemoryTransport pair, plus the far end."""
    cell_id = new_cell_id(clock)
    warden_link, queen_end = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=new_warden_id(clock), recipient=new_hive_id(clock), node_id=new_node_id(clock))
    relay = RelaySnapshotter(cell_id, warden_link, hop, clock, timeout_s=timeout_s)
    return relay, queen_end


async def _next_request(queen_end: MemoryTransport) -> Envelope:
    """Read the next request envelope the relay sent, bounded so a broken relay fails fast."""
    return await asyncio.wait_for(anext(queen_end.receive()), timeout=5.0)


def _snapshot_request(envelope: Envelope) -> CellSnapshotRequest:
    """Narrow one received Envelope's payload to CellSnapshotRequest, for typed attribute access."""
    payload = envelope.payload
    assert isinstance(payload, CellSnapshotRequest)
    return payload


def _rollback_request(envelope: Envelope) -> CellRollbackRequest:
    """Narrow one received Envelope's payload to CellRollbackRequest, for typed attribute access."""
    payload = envelope.payload
    assert isinstance(payload, CellRollbackRequest)
    return payload


async def test_snapshot_returns_the_snapshot_id_from_a_matching_reply() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.snapshot(cell))
    envelope = await _next_request(queen_end)
    request = _snapshot_request(envelope)

    reply = CellSnapshotReply(cell_id=request.cell_id, snapshot_id="snap_1", error=None)
    relay.handle_reply(reply, envelope.id)

    assert await asyncio.wait_for(task, timeout=5.0) == SnapshotId("snap_1")


async def test_snapshot_raises_unsupported_when_the_reply_carries_an_error() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.snapshot(cell))
    envelope = await _next_request(queen_end)
    request = _snapshot_request(envelope)

    relay.handle_reply(
        CellSnapshotReply(cell_id=request.cell_id, snapshot_id=None, error="no backend"),
        envelope.id,
    )

    with pytest.raises(SnapshotUnsupportedError):
        await asyncio.wait_for(task, timeout=5.0)


async def test_snapshot_raises_unsupported_when_the_link_is_already_closed() -> None:
    """Phase-7 handoff open item 8: a closed queen link takes the same fallback as a timeout."""
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    await queen_end.close()  # The peer's own clean close is final for the relay's own send.

    with pytest.raises(SnapshotUnsupportedError):
        await relay.snapshot(cell)


async def test_snapshot_raises_unsupported_on_timeout() -> None:
    clock = FakeClock()
    relay, _queen_end = _build_relay(clock, timeout_s=0.01)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)

    with pytest.raises(SnapshotUnsupportedError):
        await relay.snapshot(cell)  # No reply is ever sent; the real (wall-clock) timeout fires.


async def test_rollback_resolves_ok_true_on_a_matching_reply() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.rollback(cell, SnapshotId("snap_1")))
    envelope = await _next_request(queen_end)
    request = _rollback_request(envelope)

    relay.handle_reply(CellRollbackReply(cell_id=request.cell_id, ok=True, error=None), envelope.id)

    await asyncio.wait_for(task, timeout=5.0)  # Must not raise.


async def test_rollback_raises_unsupported_when_the_reply_is_not_ok() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.rollback(cell, SnapshotId("snap_1")))
    envelope = await _next_request(queen_end)
    request = _rollback_request(envelope)

    relay.handle_reply(
        CellRollbackReply(cell_id=request.cell_id, ok=False, error="unknown snapshot"),
        envelope.id,
    )

    with pytest.raises(SnapshotUnsupportedError):
        await asyncio.wait_for(task, timeout=5.0)


async def test_handle_reply_returns_false_for_an_unmatched_reply() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.snapshot(cell))
    envelope = await _next_request(queen_end)
    reply = CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_1", error=None)

    # No correlation at all, then one naming a request this relay never sent.
    uncorrelated = relay.handle_reply(reply, None)
    unknown = relay.handle_reply(reply, "msg_not_a_request_of_this_relay")

    assert (uncorrelated, unknown) == (False, False)
    assert not task.done()  # The real ask still waits for its own reply.
    assert relay.handle_reply(reply, envelope.id) is True
    assert await asyncio.wait_for(task, timeout=5.0) == SnapshotId("snap_1")


async def test_two_interleaved_asks_answered_out_of_order_each_get_their_own_reply() -> None:
    """Phase 5 open item 7: the second reply to arrive answers the first request, and vice versa.

    Two proposals in flight from one Warden at once; the Queen's backend finishes the second
    snapshot first. Matched by arrival order, the first ask took the second's snapshot.
    """
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    first = asyncio.ensure_future(relay.snapshot(cell))
    first_request = await _next_request(queen_end)
    second = asyncio.ensure_future(relay.snapshot(cell))
    second_request = await _next_request(queen_end)

    relay.handle_reply(
        CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_second", error=None),
        second_request.id,
    )
    relay.handle_reply(
        CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_first", error=None),
        first_request.id,
    )

    assert await asyncio.wait_for(first, timeout=5.0) == SnapshotId("snap_first")
    assert await asyncio.wait_for(second, timeout=5.0) == SnapshotId("snap_second")


async def test_a_reply_of_the_other_kind_never_resolves_an_ask() -> None:
    """A rollback reply naming a snapshot request's envelope answers nothing."""
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.snapshot(cell))
    envelope = await _next_request(queen_end)

    resolved = relay.handle_reply(
        CellRollbackReply(cell_id=cell.id, ok=True, error=None), envelope.id
    )

    assert resolved is False
    assert relay.handle_reply(
        CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_1", error=None), envelope.id
    )
    assert await asyncio.wait_for(task, timeout=5.0) == SnapshotId("snap_1")


async def test_a_reply_after_its_ask_timed_out_is_dropped() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock, timeout_s=0.01)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.snapshot(cell))
    envelope = await _next_request(queen_end)
    with pytest.raises(SnapshotUnsupportedError):
        await asyncio.wait_for(task, timeout=5.0)  # The real (wall-clock) timeout fires.

    late = relay.handle_reply(
        CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_late", error=None), envelope.id
    )

    assert late is False


async def test_a_request_is_announced_as_a_freeze_of_up_to_the_relays_timeout() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock, timeout_s=30.0)
    announced: list[float] = []

    async def announce(bound_s: float) -> None:
        announced.append(bound_s)

    relay.announce_freezes(announce)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.rollback(cell, SnapshotId("snap_1")))
    envelope = await _next_request(queen_end)
    request = _rollback_request(envelope)
    relay.handle_reply(CellRollbackReply(cell_id=request.cell_id, ok=True, error=None), envelope.id)

    await asyncio.wait_for(task, timeout=5.0)
    assert announced == [30.0]


async def test_a_wardens_snapshot_is_preceded_by_a_heartbeat_declaring_the_freeze() -> None:
    clock = FakeClock()
    deps, queen, warden_id = make_warden_deps(clock)  # A 5 s cadence: a 15 s window at the Queen.
    relay = RelaySnapshotter(new_cell_id(clock), deps.queen_link, deps.hop, clock, timeout_s=30.0)
    Warden(warden_id, dataclasses.replace(deps, snapshotter=relay))  # Binds its announcer.
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)

    task = asyncio.ensure_future(relay.snapshot(cell))
    await queen.pump_until(lambda: bool(queen.heartbeats), limit=1)  # The very first envelope.
    request = await queen.wait_for_envelope(CellSnapshotRequest)
    relay.handle_reply(
        CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_1", error=None), request.id
    )

    assert await asyncio.wait_for(task, timeout=5.0) == SnapshotId("snap_1")
    assert queen.heartbeats[0].interval_s == 30.0  # So its window at the Queen is 90 s, not 15 s.
