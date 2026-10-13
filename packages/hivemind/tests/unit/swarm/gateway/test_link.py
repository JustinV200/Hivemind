"""Tests for hivemind.swarm.gateway.link: one connected packet, spoken to and listened to.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/link.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). The device end is driven by hand with `builders.swarm.RawLink`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.link for the module under test.
    - docs/waggle/gateway-contract.md sections 5 and 6 for the link and the sender rules.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import structlog
from builders.swarm import RawLink, make_device_party, make_hive_party, make_raw_link

from hivemind.swarm.errors import DeviceRequestError, DeviceTimeoutError, GatewayUnavailableError
from hivemind.swarm.gateway import DeviceLink, HiveVoice, LinkEnds
from hivemind.swarm.gateway.frames import open_connection
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectionLostError
from waggle.ids import MessageId, new_lease_id
from waggle.messages.control import ErrorMessage
from waggle.messages.session.commands import (
    SessionClose,
    SessionOpen,
    SessionOutcome,
    SessionRequestKind,
    SessionStream,
)
from waggle.messages.session.output import SessionExit, SessionOutput
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel
from waggle.transport.memory import MemoryTransport

_TIMEOUT_S = 2.0


@pytest.fixture
async def raw() -> AsyncIterator[RawLink]:
    """A started link whose device end the test drives."""
    link = await make_raw_link()
    yield link
    await link.close()


def _open(raw: RawLink) -> SessionOpen:
    """A request to send."""
    return SessionOpen(lease_id=new_lease_id(raw.clock), reason="Testing.")


def _exit(request: SessionOpen, request_id: MessageId) -> SessionExit:
    """The terminal answer to an open."""
    return SessionExit(
        lease_id=request.lease_id,
        request_id=request_id,
        request_kind=SessionRequestKind.OPEN,
        outcome=SessionOutcome.COMPLETED,
        exit_code=None,
        signal_name=None,
        duration_s=0.0,
        size_bytes=None,
        sha256=None,
        reason="Open.",
    )


async def _ask(raw: RawLink, request: SessionOpen) -> tuple[asyncio.Task[Envelope], Envelope]:
    """Start a request on the link and return its task with the frame the device received."""
    task = asyncio.ensure_future(
        raw.link.request(request, sender=raw.hive.hive_id, timeout_s=_TIMEOUT_S)
    )
    return task, await raw.next_sent()


async def test_a_request_is_addressed_to_the_device_and_answered_by_correlation(
    raw: RawLink,
) -> None:
    request = _open(raw)
    task, sent = await _ask(raw, request)

    reply = await raw.send(_exit(request, sent.id), sent.id)

    assert (sent.sender, sent.recipient, sent.node_id) == (
        raw.hive.hive_id,
        raw.party.device_id,
        raw.hive.node_id,
    )
    assert sent.correlation_id is None
    assert (await task) == reply
    assert raw.unawaited == []


async def test_concurrent_requests_each_get_their_own_answer_even_out_of_order(
    raw: RawLink,
) -> None:
    first_request, second_request = _open(raw), _open(raw)
    first_task, first = await _ask(raw, first_request)
    second_task, second = await _ask(raw, second_request)

    second_reply = await raw.send(_exit(second_request, second.id), second.id)
    first_reply = await raw.send(_exit(first_request, first.id), first.id)

    assert (await first_task, await second_task) == (first_reply, second_reply)


async def test_a_frame_nobody_awaits_goes_to_on_frame_once_and_stamps_last_heard(
    raw: RawLink,
) -> None:
    before = raw.link.last_heard
    raw.clock.advance(7.0)

    sent = await raw.send(
        SessionOutput(
            lease_id=new_lease_id(raw.clock),
            request_id=raw.link.mint_id(),
            stream=SessionStream.STDOUT,
            chunk=b"x",
            offset=0,
            final=True,
        )
    )
    await asyncio.wait_for(raw.heard.wait(), _TIMEOUT_S)

    assert raw.unawaited == [sent]
    assert raw.link.last_heard == before + 7.0


async def test_stream_yields_every_frame_a_request_draws_up_to_the_terminal_one(
    raw: RawLink,
) -> None:
    request = _open(raw)
    seen: list[Envelope] = []

    async def consume() -> None:
        async for envelope in raw.link.stream(
            request, sender=raw.hive.hive_id, timeout_s=_TIMEOUT_S
        ):
            seen.append(envelope)

    consumer = asyncio.ensure_future(consume())
    sent = await raw.next_sent()
    chunk = await raw.send(
        SessionOutput(
            lease_id=request.lease_id,
            request_id=sent.id,
            stream=SessionStream.STDOUT,
            chunk=b"x",
            offset=0,
            final=False,
        ),
        sent.id,
    )
    terminal = await raw.send(_exit(request, sent.id), sent.id)
    await consumer

    assert seen == [chunk, terminal]


async def test_send_delivers_an_event_without_waiting_and_returns_its_id(raw: RawLink) -> None:
    correlation = raw.link.mint_id()
    event = SessionClose(lease_id=new_lease_id(raw.clock), reason="Done.")

    sent_id = await raw.link.send(event, sender=raw.hive.hive_id, correlation_id=correlation)

    arrived = await raw.next_sent()
    assert (arrived.id, arrived.correlation_id) == (sent_id, correlation)


async def test_a_control_error_answers_a_request_with_device_request_error(raw: RawLink) -> None:
    task, sent = await _ask(raw, _open(raw))

    await raw.send(
        ErrorMessage(
            code="pollen.not_found", message="No such thing.", failed_kind=None, is_retryable=False
        ),
        sent.id,
    )

    with pytest.raises(DeviceRequestError) as raised:
        await task
    assert raised.value.device_code == "pollen.not_found"


async def test_a_device_that_never_answers_raises_device_timeout(raw: RawLink) -> None:
    with pytest.raises(DeviceTimeoutError):
        await raw.link.request(_open(raw), sender=raw.hive.hive_id, timeout_s=0.05)


async def test_a_peer_that_closes_ends_the_link_and_fails_what_is_waiting(raw: RawLink) -> None:
    task, _sent = await _ask(raw, _open(raw))

    await raw.device_end.close()

    assert await asyncio.wait_for(raw.link.wait_closed(), _TIMEOUT_S) == "closed"
    with pytest.raises(GatewayUnavailableError):
        await task
    assert not raw.link.is_open
    with pytest.raises(GatewayUnavailableError):
        await raw.link.send(_open(raw), sender=raw.hive.hive_id)
    with pytest.raises(GatewayUnavailableError):
        raw.link.exchange()


async def test_a_dropped_connection_ends_the_link_with_the_errors_code(raw: RawLink) -> None:
    raw.device_end.drop()

    reason = await asyncio.wait_for(raw.link.wait_closed(), _TIMEOUT_S)

    assert reason == ConnectionLostError.code


async def test_close_ends_the_link_once_and_is_idempotent(raw: RawLink) -> None:
    await raw.link.close()
    await raw.link.close()

    assert await raw.link.wait_closed() == "closed"
    assert not raw.link.is_open


async def test_the_link_knows_the_device_and_the_queens_address(raw: RawLink) -> None:
    assert raw.link.device_id == raw.party.device_id
    assert raw.link.hive_id == raw.hive.hive_id
    assert raw.link.mint_id() != raw.link.mint_id()


async def test_a_reader_that_dies_of_a_bug_is_logged_and_ends_the_link() -> None:
    clock = FakeClock()
    hive, device = make_hive_party(clock), make_device_party(clock)
    hive_end, device_end = MemoryTransport.pair(Codec(), Codec())
    voice = HiveVoice(hive.hive_id, hive.node_id)

    async def explode(envelope: Envelope) -> None:
        raise RuntimeError("a listener bug")

    link = DeviceLink(
        open_connection(hive_end, voice, clock), LinkEnds(voice, device.device_id), clock, explode
    )
    link.start()
    heartbeat = DeviceHeartbeat(
        device_id=device.device_id,
        level=RuntimeLevel.GATEWAY,
        lease_ids=(),
        hive_started_processes=0,
        outbox_pending=0,
        uptime_s=1.0,
    )
    hop = Hop(sender=device.device_id, recipient=hive.hive_id, node_id=device.node_id)
    await device_end.send(wrap(heartbeat, hop, clock=clock))

    assert await asyncio.wait_for(link.wait_closed(), _TIMEOUT_S) == "closed"
    with structlog.testing.capture_logs() as logs:
        await link.close()
    assert [entry["event"] for entry in logs] == ["swarm.link_reader_failed"]
