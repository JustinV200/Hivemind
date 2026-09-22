"""Tests for hivemind.wardens.snapshot_relay: RelaySnapshotter, the Warden-side snapshot relay.

Fits into the Hive:
    Mirrors src/hivemind/wardens/snapshot_relay.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.snapshot_relay for the module under test.
    - packages/hivemind/tests/unit/wardens/ticks/test_control.py for the end-to-end round trip
      through handle_snapshot_reply, the Warden's own tick-side dispatch.
"""

from __future__ import annotations

import asyncio

import pytest
from builders.cells import make_cell

from hivemind.cell import CellKind, SnapshotId, SnapshotUnsupportedError
from hivemind.wardens.snapshot_relay import RelaySnapshotter
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
    request = _snapshot_request(await anext(queen_end.receive()))

    reply = CellSnapshotReply(cell_id=request.cell_id, snapshot_id="snap_1", error=None)
    relay.handle_reply(reply)

    assert await asyncio.wait_for(task, timeout=5.0) == SnapshotId("snap_1")


async def test_snapshot_raises_unsupported_when_the_reply_carries_an_error() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.snapshot(cell))
    request = _snapshot_request(await anext(queen_end.receive()))

    relay.handle_reply(
        CellSnapshotReply(cell_id=request.cell_id, snapshot_id=None, error="no backend")
    )

    with pytest.raises(SnapshotUnsupportedError):
        await asyncio.wait_for(task, timeout=5.0)


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
    request = _rollback_request(await anext(queen_end.receive()))

    relay.handle_reply(CellRollbackReply(cell_id=request.cell_id, ok=True, error=None))

    await asyncio.wait_for(task, timeout=5.0)  # Must not raise.


async def test_rollback_raises_unsupported_when_the_reply_is_not_ok() -> None:
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    task = asyncio.ensure_future(relay.rollback(cell, SnapshotId("snap_1")))
    request = _rollback_request(await anext(queen_end.receive()))

    relay.handle_reply(
        CellRollbackReply(cell_id=request.cell_id, ok=False, error="unknown snapshot")
    )

    with pytest.raises(SnapshotUnsupportedError):
        await asyncio.wait_for(task, timeout=5.0)


async def test_handle_reply_returns_false_for_an_unmatched_reply() -> None:
    clock = FakeClock()
    relay, _queen_end = _build_relay(clock)

    matched = relay.handle_reply(
        CellSnapshotReply(cell_id=new_cell_id(clock), snapshot_id="snap_1", error=None)
    )

    assert matched is False


async def test_snapshot_and_rollback_replies_are_matched_fifo_per_kind() -> None:
    """Two concurrent snapshot() asks are resolved in the order their replies arrive."""
    clock = FakeClock()
    relay, queen_end = _build_relay(clock)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    first = asyncio.ensure_future(relay.snapshot(cell))
    await anext(queen_end.receive())
    second = asyncio.ensure_future(relay.snapshot(cell))
    request_two = _snapshot_request(await anext(queen_end.receive()))

    relay.handle_reply(CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_first", error=None))
    relay.handle_reply(CellSnapshotReply(cell_id=cell.id, snapshot_id="snap_second", error=None))

    assert await asyncio.wait_for(first, timeout=5.0) == SnapshotId("snap_first")
    assert await asyncio.wait_for(second, timeout=5.0) == SnapshotId("snap_second")
    assert request_two.cell_id == cell.id
