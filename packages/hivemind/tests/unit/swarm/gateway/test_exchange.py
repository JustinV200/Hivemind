"""Tests for hivemind.swarm.gateway.exchange: one conversation with a device on a shared link.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/exchange.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The exchange is tested against a stub host, so no link or transport is involved.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.exchange for the module under test.
"""

from __future__ import annotations

import asyncio

import pytest

from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError, GatewayUnavailableError
from hivemind.swarm.gateway import exchange as exchange_module
from hivemind.swarm.gateway.exchange import Exchange, RouteTable
from waggle.clock import FakeClock
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import (
    DeviceId,
    MessageId,
    new_device_id,
    new_hive_id,
    new_lease_id,
    new_message_id,
    new_node_id,
)
from waggle.messages.base import WaggleMessage
from waggle.messages.control import ErrorMessage
from waggle.messages.session.commands import SessionOutcome, SessionRequestKind, SessionStream
from waggle.messages.session.output import SessionExit, SessionOutput

_CLOCK = FakeClock()
_DEVICE = new_device_id(_CLOCK)
_HOP = Hop(sender=_DEVICE, recipient=new_hive_id(_CLOCK), node_id=new_node_id(_CLOCK))


class _Host:
    """A stub link: a route table, an id mint and a transmit that records what was sent."""

    def __init__(self) -> None:
        self.routes = RouteTable()
        self.sent: list[tuple[WaggleMessage, MessageId, MessageId | None]] = []

    @property
    def device_id(self) -> DeviceId:
        return _DEVICE

    def mint_id(self) -> MessageId:
        return new_message_id(_CLOCK)

    async def transmit(
        self,
        payload: WaggleMessage,
        *,
        sender: str,
        correlation_id: MessageId | None,
        message_id: MessageId,
    ) -> MessageId:
        self.sent.append((payload, message_id, correlation_id))
        return message_id


def _output(request_id: MessageId) -> Envelope:
    """A `session.output` chunk correlated to `request_id`."""
    payload = SessionOutput(
        lease_id=new_lease_id(_CLOCK),
        request_id=request_id,
        stream=SessionStream.STDOUT,
        chunk=b"x",
        offset=0,
        final=False,
    )
    return wrap(payload, _HOP, clock=_CLOCK, correlation_id=request_id)


def _exit(request_id: MessageId) -> Envelope:
    """A terminal `session.exit` correlated to `request_id`."""
    payload = SessionExit(
        lease_id=new_lease_id(_CLOCK),
        request_id=request_id,
        request_kind=SessionRequestKind.OPEN,
        outcome=SessionOutcome.COMPLETED,
        exit_code=None,
        signal_name=None,
        duration_s=0.0,
        size_bytes=None,
        sha256=None,
        reason="Done.",
    )
    return wrap(payload, _HOP, clock=_CLOCK, correlation_id=request_id)


def _error(request_id: MessageId, code: str = "pollen.not_found") -> Envelope:
    """A `control.error` correlated to `request_id`."""
    payload = ErrorMessage(
        code=code, message="Refused by the device.", failed_kind=None, is_retryable=True
    )
    return wrap(payload, _HOP, clock=_CLOCK, correlation_id=request_id)


def _open() -> tuple[_Host, Exchange]:
    """A stub host and an exchange registered on it."""
    host = _Host()
    return host, Exchange(host, new_message_id(_CLOCK))


async def test_the_first_send_carries_the_exchanges_own_id_and_registers_it() -> None:
    host, exchange = _open()
    payload = _exit(exchange.id).payload

    sent_id = await exchange.send(payload, sender="s")

    assert sent_id == exchange.id
    assert host.sent == [(payload, exchange.id, None)]
    assert exchange.keys == {exchange.id}


async def test_later_sends_mint_new_ids_that_are_routed_to_the_same_exchange() -> None:
    host, exchange = _open()
    payload = _exit(exchange.id).payload
    await exchange.send(payload, sender="s")

    second = await exchange.send(payload, sender="s", correlation_id=exchange.id)

    assert second != exchange.id
    assert exchange.keys == {exchange.id, second}
    assert host.routes.target(_exit(second)) is exchange
    assert host.sent[1][2] == exchange.id


async def test_reply_skips_output_chunks_and_returns_the_terminal_frame() -> None:
    host, exchange = _open()
    await exchange.deliver(_output(exchange.id))
    terminal = _exit(exchange.id)
    await exchange.deliver(terminal)

    assert await exchange.reply(1.0) is terminal
    assert host.routes.target(terminal) is exchange


async def test_events_yields_chunks_then_the_terminal_frame_and_ends() -> None:
    _host, exchange = _open()
    chunk, terminal = _output(exchange.id), _exit(exchange.id)
    await exchange.deliver(chunk)
    await exchange.deliver(terminal)

    assert [envelope async for envelope in exchange.events(1.0)] == [chunk, terminal]


async def test_a_control_error_is_raised_as_a_device_request_error() -> None:
    _host, exchange = _open()
    await exchange.deliver(_error(exchange.id))

    with pytest.raises(DeviceRequestError) as raised:
        await exchange.reply(1.0)

    assert raised.value.device_code == "pollen.not_found"
    assert raised.value.is_retryable is True


async def test_a_silent_device_raises_device_timeout() -> None:
    _host, exchange = _open()

    with pytest.raises(DeviceTimeoutError):
        await exchange.reply(0.05)


async def test_a_link_that_ends_under_the_exchange_raises_gateway_unavailable() -> None:
    _host, exchange = _open()
    waiting = asyncio.ensure_future(exchange.reply(5.0))
    await asyncio.sleep(0)

    exchange.fail("closed")

    with pytest.raises(GatewayUnavailableError):
        await waiting


async def test_frames_delivered_before_the_link_ended_are_still_read() -> None:
    _host, exchange = _open()
    terminal = _exit(exchange.id)
    await exchange.deliver(terminal)
    exchange.fail("closed")

    assert await exchange.reply(1.0) is terminal
    with pytest.raises(GatewayUnavailableError):
        await exchange.reply(1.0)


async def test_raise_if_failed_reports_a_refusal_or_a_lost_link_and_otherwise_returns() -> None:
    _host, exchange = _open()
    exchange.raise_if_failed()

    await exchange.deliver(_error(exchange.id))
    with pytest.raises(DeviceRequestError):
        exchange.raise_if_failed()

    _host2, ended = _open()
    ended.fail("closed")
    with pytest.raises(GatewayUnavailableError):
        ended.raise_if_failed()


async def test_closing_deregisters_every_id_and_drops_what_was_queued() -> None:
    host, exchange = _open()
    payload = _exit(exchange.id).payload
    await exchange.send(payload, sender="s")
    second = await exchange.send(payload, sender="s")
    await exchange.deliver(_output(exchange.id))

    exchange.close()
    exchange.close()  # Idempotent.

    assert host.routes.target(_exit(exchange.id)) is None
    assert host.routes.target(_exit(second)) is None
    await exchange.deliver(_output(exchange.id))  # A late frame is dropped, not queued.
    with pytest.raises(DeviceTimeoutError):
        await exchange.reply(0.05)


async def test_the_context_manager_closes_the_exchange() -> None:
    host, exchange = _open()

    async with exchange as entered:
        assert entered is exchange
        assert host.routes.target(_exit(exchange.id)) is exchange

    assert host.routes.target(_exit(exchange.id)) is None


async def test_the_inbox_is_bounded_so_a_flood_waits_for_its_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(exchange_module, "EXCHANGE_QUEUE_SIZE", 1)
    _host, exchange = _open()
    await exchange.deliver(_output(exchange.id))

    blocked = asyncio.ensure_future(exchange.deliver(_output(exchange.id)))
    await asyncio.sleep(0.01)
    assert not blocked.done()

    exchange.close()  # Draining wakes the reader that was blocked delivering.
    await asyncio.wait_for(blocked, 1.0)


def test_the_route_table_routes_by_correlation_id_and_lets_go_of_a_released_exchange() -> None:
    table = RouteTable()
    host = _Host()
    exchange = Exchange(host, new_message_id(_CLOCK))
    table.register(exchange.id, exchange)

    assert table.target(_exit(exchange.id)) is exchange
    assert (
        table.target(wrap(_exit(exchange.id).payload, _HOP, clock=_CLOCK, correlation_id=None))
        is None
    )
    table.release(exchange)
    assert table.target(_exit(exchange.id)) is None


def test_failing_every_exchange_tells_each_one_once() -> None:
    host = _Host()
    first, second = Exchange(host, new_message_id(_CLOCK)), Exchange(host, new_message_id(_CLOCK))

    host.routes.fail_all("closed")

    for exchange in (first, second):
        with pytest.raises(GatewayUnavailableError):
            exchange.raise_if_failed()
