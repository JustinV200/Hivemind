"""Conformance suite for the Transport protocol: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the delivery
    contract in waggle.transport.base and runs, through the ContractLink harness beside this
    file, over both transports that ship: the in-process MemoryTransport pair and the WebSocket
    transport against a real loopback listener. Ordering within a connection, close semantics,
    oversized and malformed frame rejection, signature rejection, and outbox replay after a
    dropped link (spec section 11). A new transport joins KINDS in the harness and must pass
    here before anything uses it (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - harness.py beside this file for the ContractLink each test drives.
    - waggle.transport.base for the contract; docs/waggle/spec.md section 11 for the suite.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from waggle.clock import FakeClock
from waggle.codec import MAX_FRAME_BYTES, Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import (
    FrameTooLargeError,
    MalformedFrameError,
    MissingSignatureError,
    TransportClosedError,
    TransportError,
    UnknownSignerError,
)
from waggle.messages.control import Ping, Pong
from waggle.minting import new_node_id
from waggle.outbox import Outbox
from waggle.outbox_replay import ReplayReport, replay_outbox
from waggle.signing import Ed25519Signer
from waggle.transport.base import Transport


def _load_harness() -> ModuleType:
    """Load harness.py by path: the test tree has no packages, so it has no importable name."""
    path = Path(__file__).with_name("harness.py")
    spec = importlib.util.spec_from_file_location("waggle_transport_contract_harness", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}: no import spec for it.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if TYPE_CHECKING:
    # mypy resolves the sibling by its path-based module name (explicit_package_bases); at run
    # time the same file is loaded by path, so every name below is typed and real alike.
    from packages.waggle.tests.contracts import harness
else:
    harness = _load_harness()

MakeEnvelope = Callable[..., Envelope]
LinkFactory = Callable[[Codec, Codec], Awaitable[harness.ContractLink]]

WAIT_S = harness.WAIT_S  # Bounds every await that could hang.
EXCHANGE_COUNT = 100  # Envelopes each way: enough to expose reordering or loss on either link.
TINY_LIMIT = 64  # Below any real frame, so a normal envelope trips the sender's size check.
TWO_MEBIBYTES = 2 * MAX_FRAME_BYTES  # Twice the wire limit: refused however it arrives.
GARBAGE = b"\xff not a frame"  # Neither UTF-8 nor JSON: malformed on every transport.
QUEUED_COUNT = 3  # Envelopes appended to the outbox while the link is down.


@pytest.fixture(params=harness.KINDS)
async def make_link(request: pytest.FixtureRequest) -> AsyncIterator[LinkFactory]:
    """A factory for connected links of the parametrised kind; every link is closed afterwards."""
    opened: list[harness.ContractLink] = []

    async def factory(client_codec: Codec, server_codec: Codec) -> harness.ContractLink:
        """Open a link on the two codecs and remember it for teardown."""
        link = await harness.open_link(request.param, client_codec, server_codec)
        opened.append(link)
        return link

    yield factory
    for link in opened:
        await link.aclose()


async def _drain(transport: Transport, count: int | None = None) -> list[Envelope]:
    """The next ``count`` envelopes receive() yields, or every one to the end of the stream."""
    received: list[Envelope] = []
    receiver = transport.receive()
    try:
        async with asyncio.timeout(WAIT_S):
            async for envelope in receiver:
                received.append(envelope)
                if len(received) == count:
                    break
    finally:
        # Closed now rather than at garbage collection, so the generator's own cleanup runs
        # inside the test; the protocol promises only an AsyncIterator, hence the check.
        if isinstance(receiver, AsyncGenerator):
            await receiver.aclose()
    return received


# ──────────────────────────────────────────────────────────────────────────────
# Ordering and close semantics
# ──────────────────────────────────────────────────────────────────────────────


async def test_envelopes_arrive_in_send_order_in_both_directions(
    make_link: LinkFactory, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(plain_codec, plain_codec)
    pings = [make_envelope(Ping()) for _ in range(EXCHANGE_COUNT)]
    pongs = [
        make_envelope(Pong(received_at=ping.sent_at), correlation_id=ping.id) for ping in pings
    ]

    for ping in pings:
        await link.client.send(ping)
    for pong in pongs:
        await link.server.send(pong)

    assert await _drain(link.server, EXCHANGE_COUNT) == pings
    assert await _drain(link.client, EXCHANGE_COUNT) == pongs


async def test_a_clean_close_by_the_client_ends_the_servers_receive_and_is_final(
    make_link: LinkFactory, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(plain_codec, plain_codec)

    await link.client.close()
    await link.client.close()

    assert await _drain(link.server) == []
    assert not link.client.is_connected
    assert not link.server.is_connected
    with pytest.raises(TransportClosedError) as own:
        await link.client.send(make_envelope())
    with pytest.raises(TransportClosedError) as peer:
        await link.server.send(make_envelope())
    assert own.value.code == "waggle.transport.closed"
    assert peer.value.code == "waggle.transport.closed"


async def test_a_clean_close_by_the_server_ends_the_clients_receive_normally(
    make_link: LinkFactory, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(plain_codec, plain_codec)
    before = make_envelope()
    await link.server.send(before)

    await link.server.close()
    await link.server.close()

    # What was sent before the close still arrives; then the stream ends, cleanly.
    assert await _drain(link.client) == [before]
    with pytest.raises(TransportClosedError):
        await link.client.send(make_envelope())


# ──────────────────────────────────────────────────────────────────────────────
# Refused frames
# ──────────────────────────────────────────────────────────────────────────────


async def test_an_envelope_over_the_senders_limit_is_refused_at_send_and_nothing_arrives(
    make_link: LinkFactory, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(Codec(max_frame_bytes=TINY_LIMIT), plain_codec)

    with pytest.raises(FrameTooLargeError) as caught:
        await link.client.send(make_envelope())
    await link.client.close()

    assert caught.value.code == "waggle.codec.too_large"
    assert await _drain(link.server) == []


async def test_a_two_mebibyte_frame_is_refused_at_receive_and_closes_the_transport(
    make_link: LinkFactory, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(plain_codec, plain_codec)

    receiver = await link.inject_malformed(b"x" * TWO_MEBIBYTES)

    with pytest.raises(FrameTooLargeError) as caught:
        await _drain(receiver, 1)
    assert caught.value.code == "waggle.codec.too_large"
    assert not receiver.is_connected
    with pytest.raises(TransportClosedError):
        await receiver.send(make_envelope())


async def test_a_malformed_frame_makes_receive_raise_and_closes_the_transport(
    make_link: LinkFactory, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(plain_codec, plain_codec)

    receiver = await link.inject_malformed(GARBAGE)

    with pytest.raises(MalformedFrameError) as caught:
        await _drain(receiver, 1)
    assert caught.value.code == "waggle.codec.malformed"
    assert not receiver.is_connected
    with pytest.raises(TransportClosedError):
        await receiver.send(make_envelope())


async def test_a_signed_link_accepts_its_peer_and_rejects_a_frame_from_an_unknown_node(
    make_link: LinkFactory, signed_codec: Codec, fake_clock: FakeClock, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(signed_codec, signed_codec)
    trusted = make_envelope()
    await link.client.send(trusted)
    assert [envelope.id for envelope in await _drain(link.server, 1)] == [trusted.id]
    # Well signed, but by a node the verifier was never told to trust.
    stranger = Ed25519Signer.generate()
    hop = Hop(sender=trusted.sender, recipient=trusted.recipient, node_id=new_node_id(fake_clock))
    foreign = Codec(signer=stranger).encode(wrap(Ping(), hop, clock=fake_clock))

    receiver = await link.inject_malformed(foreign)

    with pytest.raises(UnknownSignerError) as caught:
        await _drain(receiver, 1)
    assert caught.value.code == "waggle.signature.unknown_node"
    assert not receiver.is_connected


async def test_a_signed_link_rejects_an_unsigned_frame(
    make_link: LinkFactory, signed_codec: Codec, plain_codec: Codec, make_envelope: MakeEnvelope
) -> None:
    link = await make_link(signed_codec, signed_codec)

    receiver = await link.inject_malformed(plain_codec.encode(make_envelope()))

    with pytest.raises(MissingSignatureError) as caught:
        await _drain(receiver, 1)
    assert caught.value.code == "waggle.signature.missing"
    assert not receiver.is_connected


# ──────────────────────────────────────────────────────────────────────────────
# Outbox replay after a dropped link
# ──────────────────────────────────────────────────────────────────────────────


async def test_outbox_replay_after_a_dropped_link_delivers_exactly_the_queued_tail_in_order(
    make_link: LinkFactory, plain_codec: Codec, make_envelope: MakeEnvelope, tmp_path: Path
) -> None:
    link = await make_link(plain_codec, plain_codec)
    delivered = [make_envelope(), make_envelope()]
    for envelope in delivered:
        await link.client.send(envelope)
    assert await _drain(link.server, len(delivered)) == delivered

    await link.drop_link()

    # What the bee cannot send while the link is down goes to its outbox instead.
    queued = [make_envelope() for _ in range(QUEUED_COUNT)]
    with pytest.raises(TransportError):
        await link.client.send(queued[0])
    outbox = Outbox(tmp_path / "outbox.jsonl")
    for envelope in queued:
        assert outbox.append(envelope) is True
    await link.restart()
    report = await replay_outbox(outbox, link.client)
    await link.client.close()

    assert report == ReplayReport(sent=QUEUED_COUNT, poisoned=(), stopped_by=None)
    assert await _drain(link.server) == queued
    assert len(outbox) == 0
