"""Tests for hivemind.workers.runtime.honey: the Worker's Honey channel and its Handoff deposit.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/honey.py (codingrules section 3). The channel is driven
    over a real MemoryTransport pair, the Warden's end read by hand; every wait is a FakeClock
    advance or an asyncio scheduling yield, never a real sleep.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.honey for the module under test.
    - test_loop_honey.py for the same channel inside a running WorkerRuntime.
"""

from __future__ import annotations

import asyncio
import json

from builders.cells import make_cell
from builders.honey_wire import FakeHoneyChannel, WireEnd, make_deposit_meta, make_honey_query
from builders.memory import make_handoff
from builders.workers import make_context

from hivemind.cell import CellKind, HoneyClearance
from hivemind.workers.nectar import split_deposit
from hivemind.workers.runtime.honey import (
    HANDOFF_MEDIA_TYPE,
    HONEY_QUERY_TIMEOUT_S,
    MailboxHoneyChannel,
    deposit_handoff,
)
from hivemind.workers.runtime.mailbox import Mailbox
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.errors import TransportClosedError
from waggle.ids import (
    new_event_id,
    new_message_id,
    new_node_id,
    new_task_id,
    new_warden_id,
    new_worker_id,
)
from waggle.messages import HandoffRef
from waggle.messages import HoneyClearance as WireHoneyClearance
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.honey import HoneyResponse, NectarKind
from waggle.transport.memory import MemoryTransport

_SETTLE_ROUNDS = 20  # Scheduling yields enough for a started query to reach its timeout sleep.


def _channel(clock: FakeClock) -> tuple[MailboxHoneyChannel, WireEnd]:
    """Build a channel over a Worker's mailbox, and the Warden's raw end of the same link."""
    worker_id, warden_id, node_id = new_worker_id(clock), new_warden_id(clock), new_node_id(clock)
    warden_transport, worker_transport = MemoryTransport.pair(Codec(), Codec())
    mailbox = Mailbox(
        worker_transport, Hop(sender=worker_id, recipient=warden_id, node_id=node_id), clock, 5.0
    )
    warden_end = WireEnd(
        warden_transport, Hop(sender=warden_id, recipient=worker_id, node_id=node_id), clock
    )
    return MailboxHoneyChannel(mailbox, clock), warden_end


def _response(reason: str = "Full-text search only.") -> HoneyResponse:
    return HoneyResponse(
        hits=(), token_count=0, is_truncated=False, filtered_count=0, reason=reason
    )


async def _settle() -> None:
    for _ in range(_SETTLE_ROUNDS):
        await asyncio.sleep(0)


async def test_query_sends_the_query_and_returns_the_answer_to_its_own_envelope() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)
    query = make_honey_query(clock)
    answer = _response()

    pending = asyncio.ensure_future(channel.query(query))
    sent = await warden.next()
    channel.resolve(sent.id, answer)

    assert sent.payload == query
    assert sent.correlation_id is None  # honey.query is a request: never correlated.
    assert await pending == answer


async def test_an_answer_to_another_envelope_leaves_the_query_waiting() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)

    pending = asyncio.ensure_future(channel.query(make_honey_query(clock)))
    sent = await warden.next()
    channel.resolve(new_message_id(clock), _response("someone else's"))  # Logged, not raised.
    channel.resolve(None, _response("uncorrelated"))
    await _settle()
    assert not pending.done()
    channel.resolve(sent.id, _response("mine"))

    assert (await pending).reason == "mine"


async def test_a_query_the_queen_never_answers_returns_empty_after_the_timeout() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)

    pending = asyncio.ensure_future(channel.query(make_honey_query(clock)))
    sent = await warden.next()
    await _settle()
    clock.advance(HONEY_QUERY_TIMEOUT_S)
    response = await pending
    channel.resolve(sent.id, _response("too late"))  # A late answer is dropped, never raised.

    assert response.hits == ()
    assert "did not answer" in response.reason


async def test_deposit_sends_every_chunk_in_offset_order() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)
    chunks = split_deposit(b"n" * (MAX_CHUNK_BYTES + 5), make_deposit_meta(clock))

    await channel.deposit(chunks)

    received = [await warden.next() for _ in chunks]
    assert [envelope.payload for envelope in received] == list(chunks)


async def test_deposit_handoff_sends_the_handoff_as_handoff_nectar_keyed_by_its_event() -> None:
    clock = FakeClock()
    fake = FakeHoneyChannel()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    ctx = make_context(clock=clock, cell=cell, honey=fake)
    handoff = make_handoff(clearance=HoneyClearance.C2)
    ref = HandoffRef(
        event_id=new_event_id(clock), written_at=clock.now(), clearance=WireHoneyClearance.C2
    )
    task_id = new_task_id(clock)

    await deposit_handoff(ctx, task_id, handoff, ref)

    ((chunk,),) = fake.deposits
    assert chunk.kind is NectarKind.HANDOFF
    assert chunk.media_type == HANDOFF_MEDIA_TYPE
    assert chunk.event_id == ref.event_id
    assert chunk.clearance is WireHoneyClearance.C2
    assert (chunk.task_id, chunk.worker_id, chunk.cell_id) == (task_id, ctx.worker_id, cell.id)
    assert chunk.observed_at == ref.written_at
    assert json.loads(chunk.chunk)["goal"] == handoff.goal


async def test_deposit_handoff_does_nothing_without_a_channel() -> None:
    clock = FakeClock()
    ctx = make_context(clock=clock)
    ref = HandoffRef(
        event_id=new_event_id(clock), written_at=clock.now(), clearance=WireHoneyClearance.C1
    )

    await deposit_handoff(ctx, new_task_id(clock), make_handoff(), ref)  # must not raise

    assert ctx.honey is None


async def test_deposit_handoff_logs_a_lost_link_and_never_raises() -> None:
    clock = FakeClock()
    fake = FakeHoneyChannel(fail_with=TransportClosedError("The link to the Warden closed."))
    ctx = make_context(clock=clock, honey=fake)
    ref = HandoffRef(
        event_id=new_event_id(clock), written_at=clock.now(), clearance=WireHoneyClearance.C1
    )

    await deposit_handoff(ctx, new_task_id(clock), make_handoff(), ref)  # must not raise

    assert fake.deposits == []
