"""Tests for hivemind.wardens.ticks.nuc.handle: only the Queen's order starts a job.

The handler is the tick's half of a promotion: it checks the order came down the Warden's own Queen
link and hands it to a job the Warden owns, so the tick never waits on a server. These tests
drive it through `dispatch.act` (the route the autopilot's `NUC_PROMOTE` takes) and through a
running Warden, where the order arrives on the wire and the answer is read back off it.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/nuc/handle.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.nuc.handle for the module under test.
    - builders.nuc for the rig, the ceilings helper and the orders.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from builders.nuc import (
    SERVER_ID,
    NucRig,
    allow,
    demote_order,
    make_nuc_rig,
    promote_order,
    reply_of,
)
from builders.supervision import make_inbox_item

from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.ticks.dispatch import act
from waggle.ids import new_message_id
from waggle.messages.swarm import NucOutcome, RuntimeLevel

_TIMEOUT_S = 5.0  # A reply that never comes fails the test instead of hanging the suite.


@pytest.fixture
async def rig() -> AsyncIterator[NucRig]:
    """A started Warden whose server `box` is declared and not running."""
    made = await make_nuc_rig()
    yield made
    await made.warden.stop()


async def test_an_order_from_the_queens_link_starts_a_job_that_answers_it(rig: NucRig) -> None:
    allow(rig.warden, "small")
    order = promote_order(rig.warden, "small")
    order_id = new_message_id(rig.clock)
    item = make_inbox_item(payload=order, principal="queen", id=order_id)

    await act(rig.warden, WardenAction.NUC_PROMOTE, item, None, None)

    assert len(rig.warden._tool_jobs) == 1  # The tick returned; the job is the Warden's own.
    await asyncio.wait_for(asyncio.gather(*rig.warden._tool_jobs), timeout=_TIMEOUT_S)
    envelope, reply = await reply_of(rig)
    assert envelope.correlation_id == order_id  # Correlated to the item the order arrived as.
    assert reply.outcome is NucOutcome.PROMOTED and reply.server_id == SERVER_ID


async def test_an_order_from_anywhere_but_the_queens_link_is_dropped(rig: NucRig) -> None:
    allow(rig.warden, "small")
    order = promote_order(rig.warden, "small")
    item = make_inbox_item(payload=order, principal="worker_01SOMEBEE")

    await act(rig.warden, WardenAction.NUC_PROMOTE, item, None, None)

    assert rig.warden._tool_jobs == set()  # No job, so no server and no answer.
    assert rig.warden.hosted_servers == {} and rig.queen.nuc_replies == []


async def test_the_action_with_some_other_payload_does_nothing(rig: NucRig) -> None:
    item = make_inbox_item(payload="not an order", principal="queen")

    await act(rig.warden, WardenAction.NUC_PROMOTE, item, None, None)

    assert rig.warden._tool_jobs == set()


async def test_a_running_warden_promotes_and_demotes_on_the_orders_it_is_sent(
    rig: NucRig,
) -> None:
    allow(rig.warden, "small", "mid")
    run_task = asyncio.ensure_future(rig.warden.run())

    await rig.queen.send(promote_order(rig.warden, "small", "mid"))
    _envelope, up = await asyncio.wait_for(reply_of(rig), timeout=_TIMEOUT_S)
    await rig.queen.send(demote_order(rig.warden))
    _envelope, down = await asyncio.wait_for(reply_of(rig, 2), timeout=_TIMEOUT_S)

    assert (up.outcome, up.level, up.loaded_models) == (
        NucOutcome.PROMOTED,
        RuntimeLevel.NUC,
        ("small", "mid"),
    )
    assert (down.outcome, down.level) == (NucOutcome.DEMOTED, RuntimeLevel.COLONIZED)
    assert rig.warden.hosted_servers == {}
    await rig.warden.stop()
    await asyncio.wait_for(run_task, timeout=_TIMEOUT_S)


async def test_stopping_the_warden_mid_promotion_leaves_no_server_behind(rig: NucRig) -> None:
    allow(rig.warden, "small")
    rig.door.provider.set_outage(True)  # The server starts and never answers its health probe.
    order = promote_order(rig.warden, "small")
    item = make_inbox_item(payload=order, principal="queen", id=new_message_id(rig.clock))
    await act(rig.warden, WardenAction.NUC_PROMOTE, item, None, None)
    for _ in range(30):  # Let the job start the process and settle into its health wait.
        await asyncio.sleep(0)
    session = rig.warden._session
    assert session is not None and len(session.running_pids) == 1  # type: ignore[attr-defined]

    await rig.warden.stop()

    assert not session.running_pids  # type: ignore[attr-defined]
    assert rig.forage_map.sources() == ()
