"""Tests for hivemind.wardens.ticks.tools.pending: the Warden's table of askers and awaited replies.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/tools/pending.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.tools.pending for the module under test.
    - test_honey.py (the parent directory) for the table this one is shaped like.
"""

from __future__ import annotations

from hivemind.wardens.ticks.tools import Asker, ToolRelay
from hivemind.wardens.ticks.tools.pending import MAX_PENDING_TOOL_REQUESTS
from waggle.clock import FakeClock
from waggle.ids import new_message_id, new_tool_id, new_worker_id
from waggle.messages.control import ErrorMessage

_ERROR = ErrorMessage(
    code="hive.tool.rejected",
    message="The tool failed a gate of the Quarantine Comb.",
    failed_kind="tool.quarantine",
    is_retryable=False,
)


def _asker(clock: FakeClock) -> Asker:
    return Asker(new_worker_id(clock), new_message_id(clock))


def test_an_asker_is_found_by_its_tool_or_its_forwarded_envelope_and_only_once() -> None:
    clock = FakeClock()
    relay = ToolRelay()
    by_tool, by_envelope = new_tool_id(clock), new_tool_id(clock)
    forwarded = new_message_id(clock)
    first, second = _asker(clock), _asker(clock)
    relay.remember(by_tool, new_message_id(clock), first)
    relay.remember(by_envelope, forwarded, second)

    assert relay.take_by_tool(by_tool) == first
    assert relay.take_by_tool(by_tool) is None
    assert relay.take_by_envelope(forwarded) == second
    assert relay.take_by_envelope(forwarded) is None
    assert relay.take_by_envelope(None) is None
    assert len(relay) == 0


def test_the_oldest_asker_is_forgotten_once_the_table_is_full() -> None:
    clock = FakeClock()
    relay = ToolRelay()
    tools = [new_tool_id(clock) for _ in range(MAX_PENDING_TOOL_REQUESTS + 1)]

    for tool_id in tools:
        relay.remember(tool_id, new_message_id(clock), _asker(clock))

    assert len(relay) == MAX_PENDING_TOOL_REQUESTS
    assert relay.take_by_tool(tools[0]) is None
    assert relay.take_by_tool(tools[-1]) is not None


async def test_a_reply_resolves_the_future_registered_for_its_envelope_once() -> None:
    clock = FakeClock()
    relay = ToolRelay()
    sent = new_message_id(clock)
    waiter = relay.expect(sent)

    assert relay.resolve(sent, _ERROR)
    assert await waiter == _ERROR
    assert not relay.resolve(sent, _ERROR)  # Already answered: a late duplicate is stray.
    assert not relay.resolve(new_message_id(clock), _ERROR)
    assert not relay.resolve(None, _ERROR)


async def test_a_discarded_future_is_no_longer_resolvable() -> None:
    clock = FakeClock()
    relay = ToolRelay()
    sent = new_message_id(clock)
    waiter = relay.expect(sent)

    relay.discard(sent)

    assert not relay.resolve(sent, _ERROR)
    waiter.cancel()
