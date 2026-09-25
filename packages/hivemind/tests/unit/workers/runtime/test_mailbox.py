"""Tests for hivemind.workers.runtime.mailbox: wrapping an envelope before it is sent.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/mailbox.py (codingrules section 3). The rest of the
    Mailbox (the receive/heartbeat race, `ask`) is exercised through a running WorkerRuntime in
    test_loop.py; this module covers `envelope_for`/`send_envelope` (roadmap step 7.8), which a
    caller uses to know an envelope's id before it leaves.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.mailbox for the module under test.
"""

from __future__ import annotations

from builders.honey_wire import WireEnd, make_honey_query

from hivemind.workers.runtime.mailbox import Mailbox
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_message_id, new_node_id, new_warden_id, new_worker_id
from waggle.messages.honey import HoneyResponse
from waggle.transport.memory import MemoryTransport


def _mailbox(clock: FakeClock) -> tuple[Mailbox, WireEnd, Hop]:
    worker_id, warden_id, node_id = new_worker_id(clock), new_warden_id(clock), new_node_id(clock)
    warden_transport, worker_transport = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=worker_id, recipient=warden_id, node_id=node_id)
    warden_hop = Hop(sender=warden_id, recipient=worker_id, node_id=node_id)
    return (
        Mailbox(worker_transport, hop, clock, 5.0),
        WireEnd(warden_transport, warden_hop, clock),
        hop,
    )


async def test_envelope_for_wraps_with_the_mailboxs_own_hop_and_sends_nothing() -> None:
    clock = FakeClock()
    mailbox, _warden, hop = _mailbox(clock)
    query = make_honey_query(clock)

    envelope = mailbox.envelope_for(query)

    assert (envelope.sender, envelope.recipient, envelope.node_id) == (
        hop.sender,
        hop.recipient,
        hop.node_id,
    )
    assert envelope.payload == query
    assert envelope.kind == "honey.query"


async def test_send_envelope_delivers_exactly_the_envelope_it_was_given() -> None:
    clock = FakeClock()
    mailbox, warden, _hop = _mailbox(clock)
    reply_to = new_message_id(clock)
    response = HoneyResponse(
        hits=(), token_count=0, is_truncated=False, filtered_count=0, reason="none"
    )
    envelope = mailbox.envelope_for(response, correlation_id=reply_to)

    await mailbox.send_envelope(envelope)

    received = await warden.next()
    assert received.id == envelope.id
    assert received.correlation_id == reply_to
    assert received.payload == response
