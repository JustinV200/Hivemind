"""Tests for hivemind.queen.swarm_gate.colonize.nuc.desk: what device Wardens declare and answer.

The desk is the gate's ear on a device Warden's connection. It notes the model servers a capacity
report declares (and lets the report go on to the Queen), consumes the one kind that answers its
own order, and carries an order to the Warden and back: a reply is claimed by the id of the order
it answers, a Warden speaks only for its own device, and nothing waits for ever.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/colonize/nuc/desk.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.colonize.nuc.desk for the module under test.
    - builders.queen for the Queen's link to a Warden and the Warden's end of it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from builders.swarm import make_capacity_report, make_device_party, make_swarm_node
from builders.swarm.parties import DeviceParty

from hivemind.queen.deps import WardenLink
from hivemind.queen.swarm_gate.colonize.errors import ColonizeStepError
from hivemind.queen.swarm_gate.colonize.nuc import desk as desk_module
from hivemind.queen.swarm_gate.colonize.nuc.desk import NucDesk
from hivemind.swarm import SwarmNode
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import MessageId, new_cell_id, new_device_id, new_hive_id, new_message_id
from waggle.messages.base import WaggleMessage
from waggle.messages.forage import ModelServerReport
from waggle.messages.swarm import NucAction, NucOutcome, NucPromote, NucPromoted, RuntimeLevel
from waggle.transport.memory import MemoryTransport

_CLOCK = FakeClock()
_TIMEOUT_S = 5.0  # A reply that never comes fails the test instead of hanging the suite.


def _scene() -> tuple[NucDesk, SwarmNode, DeviceParty, WardenLink, AsyncIterator[Envelope]]:
    """A desk, one device, the Queen's link to its Warden and what the Warden receives on it."""
    party = make_device_party(_CLOCK)
    node = make_swarm_node(party, _CLOCK)
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    hop = Hop(sender=new_hive_id(_CLOCK), recipient=node.warden_id, node_id=node.node_id)
    link = WardenLink(warden_id=node.warden_id, cell=node.as_cell(), transport=queen_end, hop=hop)
    return NucDesk(), node, party, link, warden_end.receive()


def _order(node: SwarmNode, deadline_s: float = 30.0) -> NucPromote:
    return NucPromote(
        cell_id=node.cell_id,
        device_id=node.device_id,
        action=NucAction.DEMOTE,
        server_id=None,
        initial_models=(),
        deadline_s=deadline_s,
        reason="A test order.",
    )


def _reply(order: NucPromote, **overrides: object) -> NucPromoted:
    fields: dict[str, object] = {
        "cell_id": order.cell_id,
        "device_id": order.device_id,
        "action": order.action,
        "outcome": NucOutcome.DEMOTED,
        "level": RuntimeLevel.COLONIZED,
        "server_id": None,
        "loaded_models": (),
        "reason": "Scripted.",
    }
    fields.update(overrides)
    return NucPromoted(**fields)


def _frame(
    payload: WaggleMessage, node: SwarmNode, correlation_id: MessageId | None = None
) -> Envelope:
    hop = Hop(sender=node.warden_id, recipient=node.device_id, node_id=node.node_id)
    return wrap(payload, hop, clock=_CLOCK, correlation_id=correlation_id)


async def _sent(inbox: AsyncIterator[Envelope]) -> Envelope:
    """The order the Queen sent the Warden, read off the Warden's end of the link."""
    return await asyncio.wait_for(anext(inbox), timeout=_TIMEOUT_S)


def test_a_capacity_report_names_the_servers_and_still_goes_on_to_the_queen() -> None:
    desk, node, party, _link, _inbox = _scene()
    servers = (ModelServerReport(server_id="box", sources=()),)
    report = make_capacity_report(party, model_servers=servers)

    consumed = desk.heard(node, _frame(report, node))

    assert consumed is False  # The Queen still hears it.
    assert desk.declared(node.device_id) == ("box",)


def test_a_device_that_declared_nothing_declares_no_server() -> None:
    desk, node, _party, _link, _inbox = _scene()

    assert desk.declared(node.device_id) == ()


def test_the_latest_report_replaces_the_servers_declared_before_it() -> None:
    desk, node, party, _link, _inbox = _scene()
    first = make_capacity_report(
        party, model_servers=(ModelServerReport(server_id="box", sources=()),)
    )
    desk.heard(node, _frame(first, node))

    desk.heard(node, _frame(make_capacity_report(party), node))

    assert desk.declared(node.device_id) == ()


def test_a_report_for_another_cell_declares_nothing() -> None:
    desk, node, party, _link, _inbox = _scene()
    stranger = make_capacity_report(
        party,
        cell_id=new_cell_id(_CLOCK),
        model_servers=(ModelServerReport(server_id="box", sources=()),),
    )

    desk.heard(node, _frame(stranger, node))

    assert desk.declared(node.device_id) == ()  # A Warden speaks only for its own Cell.


def test_forgetting_a_device_forgets_what_it_declared() -> None:
    desk, node, party, _link, _inbox = _scene()
    report = make_capacity_report(
        party, model_servers=(ModelServerReport(server_id="box", sources=()),)
    )
    desk.heard(node, _frame(report, node))

    desk.forget(node.device_id)

    assert desk.declared(node.device_id) == ()


def test_any_other_frame_is_not_the_gates() -> None:
    desk, node, _party, _link, _inbox = _scene()

    assert desk.heard(node, _frame(_order(node), node)) is False


def test_an_answer_nobody_waits_for_is_consumed_and_dropped() -> None:
    desk, node, _party, _link, _inbox = _scene()
    order = _order(node)

    consumed = desk.heard(node, _frame(_reply(order), node, new_message_id(_CLOCK)))

    assert consumed is True  # Never the Queen's: her autopilot has no rule for it.


async def test_an_order_is_answered_by_the_reply_correlated_to_it() -> None:
    desk, node, _party, link, inbox = _scene()
    order = _order(node)

    asking = asyncio.create_task(desk.ask(link, order, _CLOCK))
    sent = await _sent(inbox)
    assert sent.payload == order
    assert desk.heard(node, _frame(_reply(order), node, sent.id)) is True

    reply = await asyncio.wait_for(asking, timeout=_TIMEOUT_S)
    assert reply.outcome is NucOutcome.DEMOTED


async def test_a_reply_that_names_another_device_does_not_answer_the_order() -> None:
    desk, node, _party, link, inbox = _scene()
    order = _order(node)
    other = make_swarm_node(make_device_party(_CLOCK), _CLOCK)

    asking = asyncio.create_task(desk.ask(link, order, _CLOCK))
    sent = await _sent(inbox)
    assert desk.heard(other, _frame(_reply(order), other, sent.id)) is True  # Consumed ...

    assert not asking.done()  # ... but a Warden answers only for its own device's orders.
    assert desk.heard(node, _frame(_reply(order), node, sent.id)) is True
    await asyncio.wait_for(asking, timeout=_TIMEOUT_S)


async def test_a_reply_about_another_cell_does_not_answer_the_order() -> None:
    desk, node, _party, link, inbox = _scene()
    order = _order(node)

    asking = asyncio.create_task(desk.ask(link, order, _CLOCK))
    sent = await _sent(inbox)
    wrong = _reply(order, cell_id=new_cell_id(_CLOCK))
    assert desk.heard(node, _frame(wrong, node, sent.id)) is True

    assert not asking.done()
    asking.cancel()
    await asyncio.gather(asking, return_exceptions=True)


async def test_losing_the_wardens_connection_ends_the_wait_at_once() -> None:
    desk, node, _party, link, inbox = _scene()

    asking = asyncio.create_task(desk.ask(link, _order(node), _CLOCK))
    await _sent(inbox)
    desk.lost(node.device_id)

    with pytest.raises(ColonizeStepError) as raised:
        await asyncio.wait_for(asking, timeout=_TIMEOUT_S)
    assert raised.value.word == "link_lost"


async def test_losing_another_devices_connection_ends_nothing() -> None:
    desk, node, _party, link, inbox = _scene()
    asking = asyncio.create_task(desk.ask(link, _order(node), _CLOCK))
    sent = await _sent(inbox)

    desk.lost(new_device_id(_CLOCK))

    assert not asking.done()
    desk.heard(node, _frame(_reply(_order(node)), node, sent.id))
    await asyncio.wait_for(asking, timeout=_TIMEOUT_S)


async def test_an_order_to_a_link_that_is_closed_is_a_lost_link() -> None:
    desk, node, _party, link, _end = _scene()
    await link.transport.close()

    with pytest.raises(ColonizeStepError) as raised:
        await desk.ask(link, _order(node), _CLOCK)

    assert raised.value.word == "link_lost"


async def test_a_warden_that_never_answers_is_given_up_on_after_the_deadline_and_a_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desk_module, "REPLY_MARGIN_S", 0.05)
    desk, node, _party, link, _end = _scene()

    with pytest.raises(ColonizeStepError) as raised:
        await desk.ask(link, _order(node, deadline_s=0.05), _CLOCK)

    assert raised.value.word == "warden_silent"


async def test_a_late_reply_finds_nobody_waiting() -> None:
    desk, node, _party, link, inbox = _scene()
    order = _order(node)
    asking = asyncio.create_task(desk.ask(link, order, _CLOCK))
    sent = await _sent(inbox)
    desk.heard(node, _frame(_reply(order), node, sent.id))
    await asking

    assert desk.heard(node, _frame(_reply(order), node, sent.id)) is True  # Dropped, not queued.
