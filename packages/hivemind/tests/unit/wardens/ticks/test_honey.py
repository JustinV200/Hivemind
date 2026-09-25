"""Tests for hivemind.wardens.ticks.honey: relaying the Honey Store's traffic through a Warden.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/honey.py (codingrules section 3). Drives the relay
    directly against a real Warden, reading both of its links raw: the Queen's end of its queen
    link and a sub-bee's end of that sub-bee's own link (`builders.honey_wire.WireEnd`), so the
    envelope ids, addresses and correlations of every hop are asserted, not just the payloads.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.honey for the module under test.
    - test_warden_forwarding.py for the Question/Answer relay tests this module is shaped like.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from builders.honey_wire import WireEnd, make_deposit_meta, make_honey_query
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_context, make_outcome, yield_then

from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.honey import (
    MAX_PENDING_HONEY_QUERIES,
    QUEEN_UNREACHABLE_REASON,
    REFUSED_QUERY_REASON,
    HoneyRelay,
    handle_honey_item,
)
from hivemind.wardens.warden import Warden
from hivemind.workers.nectar import split_deposit
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_hive_id, new_message_id, new_task_id, new_warden_id, new_worker_id
from waggle.messages.control.protocol import ErrorMessage
from waggle.messages.honey import HoneyQuery, HoneyResponse
from waggle.transport.memory import MemoryTransport

_QUEEN = "queen"  # The principal a Warden gives every item from its own Queen link.


@dataclass(frozen=True, slots=True)
class _Rig:
    """One Warden, the Queen's raw end of its link, and one sub-bee with its own raw end."""

    warden: Warden
    queen: WireEnd
    sub_bee: SubBee
    worker: WireEnd


async def _rig() -> _Rig:
    clock = FakeClock()
    hive_id, warden_id = new_hive_id(clock), new_warden_id(clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    deps, _queen_end, _ = make_warden_deps(clock, warden_id=warden_id, queen_link=warden_transport)
    queen_hop = Hop(sender=hive_id, recipient=warden_id, node_id=deps.identity.node_id)
    warden = Warden(warden_id, deps)
    sub_bee, worker = await _sub_bee(warden, clock)
    return _Rig(warden, WireEnd(queen_transport, queen_hop, clock), sub_bee, worker)


async def _sub_bee(warden: Warden, clock: FakeClock) -> tuple[SubBee, WireEnd]:
    """Register one sub-bee over a real pair; its runtime exists but is never run."""
    worker_id = new_worker_id(clock)
    link, worker_transport = MemoryTransport.pair(Codec(), Codec())
    worker_hop = Hop(
        sender=worker_id, recipient=warden._warden_id, node_id=warden._deps.hop.node_id
    )
    runtime = WorkerRuntime(
        make_context(clock=clock, worker_id=worker_id),
        ScriptedWorker(yield_then(make_outcome)),
        RuntimeDeps(
            transport=worker_transport, hop=worker_hop, heartbeat_interval_s=5.0, clock=clock
        ),
    )
    done: asyncio.Task[None] = asyncio.ensure_future(asyncio.sleep(0))
    await done
    assignment = make_assignment(clock=clock)
    sub_bee = SubBee(
        worker_id=worker_id,
        task_id=assignment.task_id,
        assignment=assignment,
        attempt=1,
        state=WorkerState.RUNNING,
        binding="worker",
        last_handoff=None,
        link=link,
        runtime=runtime,
        runtime_task=done,
    )
    warden._sub_bees[worker_id] = sub_bee
    return sub_bee, WireEnd(worker_transport, worker_hop, clock)


def _own_query(rig: _Rig) -> HoneyQuery:
    clock = rig.warden._deps.clock
    return make_honey_query(clock, requester=rig.sub_bee.worker_id, task_id=rig.sub_bee.task_id)


def _response(reason: str = "One hit.") -> HoneyResponse:
    return HoneyResponse(
        hits=(), token_count=0, is_truncated=False, filtered_count=0, reason=reason
    )


async def _assert_queen_got_nothing(rig: _Rig) -> None:
    """Close the Warden's queen link: anything it sent arrives first, then the stream ends."""
    await rig.warden._deps.queen_link.close()
    with pytest.raises(StopAsyncIteration):
        await rig.queen.next()


async def test_a_query_goes_up_unchanged_and_its_answer_comes_back_to_its_asker() -> None:
    rig = await _rig()
    query = _own_query(rig)
    asked = rig.worker.envelope_for(query)

    await handle_honey_item(rig.warden, to_inbox_item(asked, rig.sub_bee.worker_id), rig.sub_bee)
    forwarded = await rig.queen.next()
    answer = rig.queen.envelope_for(_response(), correlation_id=forwarded.id)
    await handle_honey_item(rig.warden, to_inbox_item(answer, _QUEEN), None)
    relayed = await rig.worker.next()

    assert forwarded.payload == query
    assert (forwarded.id, forwarded.sender) != (asked.id, asked.sender)
    assert forwarded.correlation_id is None
    assert relayed.payload == _response()
    assert relayed.correlation_id == asked.id
    assert relayed.recipient == rig.sub_bee.worker_id
    assert len(rig.warden._honey_relay) == 0


@pytest.mark.parametrize("field", ["requester", "task_id"])
async def test_a_query_naming_another_bee_or_task_is_answered_here_never_forwarded(
    field: str,
) -> None:
    rig = await _rig()
    clock = rig.warden._deps.clock
    other = new_worker_id(clock) if field == "requester" else new_task_id(clock)
    query = _own_query(rig).model_copy(update={field: other})
    asked = rig.worker.envelope_for(query)

    await handle_honey_item(rig.warden, to_inbox_item(asked, rig.sub_bee.worker_id), rig.sub_bee)

    refused = await rig.worker.next()
    assert refused.payload == _response(REFUSED_QUERY_REASON)
    assert refused.correlation_id == asked.id
    await _assert_queen_got_nothing(rig)


async def test_a_deposit_chunk_goes_up_unchanged_and_another_bees_is_dropped() -> None:
    rig = await _rig()
    clock = rig.warden._deps.clock
    own = make_deposit_meta(clock, worker_id=rig.sub_bee.worker_id, task_id=rig.sub_bee.task_id)
    (chunk,) = split_deposit(b"a finding", own)
    (forged,) = split_deposit(b"a forged finding", make_deposit_meta(clock))
    principal = rig.sub_bee.worker_id

    await handle_honey_item(
        rig.warden, to_inbox_item(rig.worker.envelope_for(chunk), principal), rig.sub_bee
    )
    await handle_honey_item(
        rig.warden, to_inbox_item(rig.worker.envelope_for(forged), principal), rig.sub_bee
    )

    forwarded = await rig.queen.next()
    assert forwarded.payload == chunk
    await _assert_queen_got_nothing(rig)


async def test_an_unmatched_or_orphaned_response_is_dropped_never_raised() -> None:
    rig = await _rig()
    clock = rig.warden._deps.clock
    stray = rig.queen.envelope_for(_response(), correlation_id=new_message_id(clock))
    asked = rig.worker.envelope_for(_own_query(rig))
    await handle_honey_item(rig.warden, to_inbox_item(asked, rig.sub_bee.worker_id), rig.sub_bee)
    forwarded = await rig.queen.next()
    del rig.warden._sub_bees[rig.sub_bee.worker_id]  # The asker ends before its answer arrives.
    orphan = rig.queen.envelope_for(_response(), correlation_id=forwarded.id)

    await handle_honey_item(rig.warden, to_inbox_item(stray, _QUEEN), None)
    await handle_honey_item(rig.warden, to_inbox_item(orphan, _QUEEN), None)

    await rig.sub_bee.link.close()
    with pytest.raises(StopAsyncIteration):
        await rig.worker.next()


async def test_traffic_arriving_the_wrong_way_is_dropped() -> None:
    rig = await _rig()
    query = rig.queen.envelope_for(_own_query(rig))
    response = rig.worker.envelope_for(_response(), correlation_id=new_message_id(FakeClock()))

    await handle_honey_item(rig.warden, to_inbox_item(query, _QUEEN), None)
    await handle_honey_item(rig.warden, to_inbox_item(response, rig.sub_bee.worker_id), rig.sub_bee)

    await _assert_queen_got_nothing(rig)


async def test_a_queen_error_about_a_relayed_deposit_is_only_logged() -> None:
    rig = await _rig()
    error = ErrorMessage(
        code="hivemind.honey_store.cell_mismatch",
        message="The deposit named another Cell.",
        failed_kind="honey.nectar_deposit",
        is_retryable=False,
    )
    envelope = rig.queen.envelope_for(error, correlation_id=new_message_id(FakeClock()))

    await handle_honey_item(rig.warden, to_inbox_item(envelope, _QUEEN), None)  # must not raise

    await _assert_queen_got_nothing(rig)


async def test_a_disconnected_warden_answers_a_query_itself_and_drops_a_chunk() -> None:
    """Codingrules 8.8: a Queen link gone is never a reason to end the Warden's tick."""
    rig = await _rig()
    await rig.queen.transport.close()
    asked = rig.worker.envelope_for(_own_query(rig))
    own = make_deposit_meta(
        rig.warden._deps.clock, worker_id=rig.sub_bee.worker_id, task_id=rig.sub_bee.task_id
    )
    (chunk,) = split_deposit(b"a finding", own)
    principal = rig.sub_bee.worker_id

    await handle_honey_item(rig.warden, to_inbox_item(asked, principal), rig.sub_bee)
    await handle_honey_item(
        rig.warden, to_inbox_item(rig.worker.envelope_for(chunk), principal), rig.sub_bee
    )

    answered = await rig.worker.next()
    assert answered.payload == _response(QUEEN_UNREACHABLE_REASON)
    assert answered.correlation_id == asked.id
    assert len(rig.warden._honey_relay) == 0


async def test_an_answer_for_a_sub_bee_whose_link_closed_is_dropped() -> None:
    rig = await _rig()
    asked = rig.worker.envelope_for(_own_query(rig))
    await handle_honey_item(rig.warden, to_inbox_item(asked, rig.sub_bee.worker_id), rig.sub_bee)
    forwarded = await rig.queen.next()
    await rig.worker.transport.close()  # The sub-bee ends; its end of the link with it.
    answer = rig.queen.envelope_for(_response(), correlation_id=forwarded.id)

    await handle_honey_item(rig.warden, to_inbox_item(answer, _QUEEN), None)  # must not raise

    assert len(rig.warden._honey_relay) == 0


def test_the_relay_forgets_its_oldest_query_once_full() -> None:
    clock = FakeClock()
    relay = HoneyRelay()
    forwarded = [new_message_id(clock) for _ in range(MAX_PENDING_HONEY_QUERIES + 1)]

    for forwarded_id in forwarded:
        relay.remember(forwarded_id, new_worker_id(clock), new_message_id(clock))

    assert len(relay) == MAX_PENDING_HONEY_QUERIES
    assert relay.take(forwarded[0]) is None
    assert relay.take(forwarded[-1]) is not None
    assert relay.take(None) is None
