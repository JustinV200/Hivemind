"""Tests for hivemind.wardens.ticks.dispatch: routing a decided WardenAction to its handler.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/dispatch.py (codingrules section 3). The routing moved
    here from `warden.py`'s own `_act` unchanged in behaviour, so every existing Warden test
    (test_warden_*.py) still exercises it end to end through a running Warden; this module
    covers the routes it gained (FORWARD_HONEY, roadmap step 7.8) and its no-op rule directly.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.dispatch for the module under test.
"""

from __future__ import annotations

import pytest
from builders.honey_wire import WireEnd, make_honey_query
from builders.supervision import make_inbox_item
from builders.wardens import make_warden_deps

from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.ticks.dispatch import act
from hivemind.wardens.warden import Warden
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import new_cell_id, new_grant_id, new_hive_id, new_warden_id
from waggle.messages.forage import GrantIssued
from waggle.transport.memory import MemoryTransport


def _warden() -> tuple[Warden, WireEnd]:
    """Build a Warden whose queen link the test reads raw from the Queen's end."""
    clock = FakeClock()
    hive_id, warden_id = new_hive_id(clock), new_warden_id(clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    deps, _queen_end, _ = make_warden_deps(clock, warden_id=warden_id, queen_link=warden_transport)
    queen_hop = Hop(sender=hive_id, recipient=warden_id, node_id=deps.identity.node_id)
    return Warden(warden_id, deps), WireEnd(queen_transport, queen_hop, clock)


def _grant(warden: Warden) -> GrantIssued:
    clock = warden._deps.clock
    return GrantIssued(
        grant_id=new_grant_id(clock),
        holder=warden._warden_id,
        cell_id=new_cell_id(clock),
        task_id=None,
        revision=0,
        allowed=(),
        seats=(),
        token_budget=1_000,
        spend_budget=1.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=clock.now(),
        reason="a test grant",
    )


async def test_a_record_action_hands_a_grant_to_the_assign_handler() -> None:
    warden, _queen = _warden()
    grant = _grant(warden)

    await act(warden, WardenAction.RECORD, make_inbox_item(payload=grant), None, None)

    assert warden._grants[grant.grant_id] is grant


async def test_forward_honey_reaches_the_honey_relay() -> None:
    """Roadmap step 7.8: a Queen-link query is the wrong direction; the relay drops it."""
    warden, queen = _warden()
    query = make_honey_query(warden._deps.clock)
    item = make_inbox_item(payload=query, principal="queen", payload_kind="honey.query")

    await act(warden, WardenAction.FORWARD_HONEY, item, None, None)

    await warden._deps.queen_link.close()
    with pytest.raises(StopAsyncIteration):
        await queen.next()


async def test_an_action_whose_payload_does_not_fit_does_nothing() -> None:
    warden, _queen = _warden()
    grant = _grant(warden)

    # SPAWN takes a TaskAssign; a grant under it is a stale or misrouted item, not an order.
    await act(warden, WardenAction.SPAWN, make_inbox_item(payload=grant), None, None)

    assert warden._grants == {}
    assert warden._pending == {}
