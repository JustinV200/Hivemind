"""Tests for hivemind.workers.runtime.tools: the Worker's Tool channel over its mailbox.

Fits into the Hive:
    Mirrors src/hivemind/workers/runtime/tools.py (codingrules section 3). The channel is driven
    over a real MemoryTransport pair with the Warden's end read by hand; every wait is a FakeClock
    advance or an asyncio scheduling yield, never a real sleep.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.runtime.tools for the module under test.
    - test_loop_tools.py for the same channel inside a running WorkerRuntime.
"""

from __future__ import annotations

import asyncio

from builders.comb_tools import make_comb_package, make_promoted
from builders.honey_wire import WireEnd
from builders.royal_jelly import make_tool_request

from hivemind.workers.runtime.mailbox import Mailbox
from hivemind.workers.runtime.tools import (
    TIMEOUT_ERROR_CODE,
    TOOL_REQUEST_TIMEOUT_S,
    MailboxToolChannel,
)
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_message_id, new_node_id, new_tool_id, new_warden_id, new_worker_id
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import ToolPromoted, ToolRequest
from waggle.transport.memory import MemoryTransport

_SETTLE_ROUNDS = 20  # Scheduling yields enough for a started request to reach its timeout sleep.


def _channel(clock: FakeClock) -> tuple[MailboxToolChannel, WireEnd]:
    """Build a channel over a Worker's mailbox, and the Warden's raw end of the same link."""
    worker_id, warden_id, node_id = new_worker_id(clock), new_warden_id(clock), new_node_id(clock)
    warden_transport, worker_transport = MemoryTransport.pair(Codec(), Codec())
    mailbox = Mailbox(
        worker_transport, Hop(sender=worker_id, recipient=warden_id, node_id=node_id), clock, 5.0
    )
    warden_end = WireEnd(
        warden_transport, Hop(sender=warden_id, recipient=worker_id, node_id=node_id), clock
    )
    return MailboxToolChannel(mailbox, clock, warden_id), warden_end


def _promoted_for(request: ToolRequest) -> ToolPromoted:
    """The announcement that fulfils `request`: the tool's id carried through."""
    package = make_comb_package(name=request.name)
    return make_promoted(package).model_copy(update={"tool_id": request.tool_id})


def _error(message: str = "The tool failed the Quarantine Comb.") -> ErrorMessage:
    return ErrorMessage(
        code="hive.tool.rejected", message=message, failed_kind="tool.request", is_retryable=False
    )


async def _settle() -> None:
    for _ in range(_SETTLE_ROUNDS):
        await asyncio.sleep(0)


async def test_the_channel_names_the_warden_at_the_other_end_of_the_link() -> None:
    clock = FakeClock()
    channel, _warden = _channel(clock)

    assert str(channel.warden_id).startswith("warden_")


async def test_request_sends_the_request_and_returns_the_promotion_with_its_tool_id() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)
    request = make_tool_request(clock)

    pending = asyncio.ensure_future(channel.request(request))
    sent = await warden.next()
    promoted = _promoted_for(request)
    channel.resolve_promoted(promoted)

    assert sent.payload == request
    assert sent.correlation_id is None  # tool.request is a request: never correlated.
    assert await pending == promoted


async def test_an_error_correlated_to_the_request_envelope_resolves_it() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)

    pending = asyncio.ensure_future(channel.request(make_tool_request(clock)))
    sent = await warden.next()
    refusal = _error()
    channel.resolve_error(sent.id, refusal)

    assert await pending == refusal


async def test_answers_for_another_request_leave_this_one_waiting() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)
    request = make_tool_request(clock)

    pending = asyncio.ensure_future(channel.request(request))
    await warden.next()
    # Strays, each logged and dropped: another tool's promotion, an uncorrelated error, a
    # correlation to an envelope this channel never sent.
    channel.resolve_promoted(_promoted_for(make_tool_request(clock, tool_id=new_tool_id(clock))))
    channel.resolve_error(None, _error("uncorrelated"))
    channel.resolve_error(new_message_id(clock), _error("someone else's"))
    await _settle()
    assert not pending.done()
    channel.resolve_promoted(_promoted_for(request))

    assert isinstance(await pending, ToolPromoted)


async def test_a_request_nobody_answers_is_answered_as_a_retryable_timeout_error() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)
    request = make_tool_request(clock)

    pending = asyncio.ensure_future(channel.request(request))
    await warden.next()
    await _settle()
    clock.advance(TOOL_REQUEST_TIMEOUT_S)
    answer = await pending
    channel.resolve_promoted(_promoted_for(request))  # A late answer is dropped, never raised.

    assert isinstance(answer, ErrorMessage)
    assert answer.code == TIMEOUT_ERROR_CODE and answer.is_retryable is True


async def test_whichever_answer_comes_first_resolves_and_the_other_is_dropped() -> None:
    clock = FakeClock()
    channel, warden = _channel(clock)
    request = make_tool_request(clock)

    pending = asyncio.ensure_future(channel.request(request))
    sent = await warden.next()
    channel.resolve_promoted(_promoted_for(request))
    channel.resolve_error(sent.id, _error())  # Second answer: dropped.

    assert isinstance(await pending, ToolPromoted)
