"""Test one connection to the Queen: the pinned dial, verified frames, and swappable keys.

The Pollen Packet (the thin gateway program on a device the Hive borrows) dials the Queen (the
Hive's central orchestrator) and never listens (gateway-contract section 5). A ``Link`` is one such
connection: ``wss://`` with the certificate fingerprint from the invite, which replaces chain and
name validation (a different certificate closes the connection before any frame is sent), or plain
``ws://`` on loopback. Every frame that arrives is verified against the trusted nodes' keys by the
codec before the packet reads it, and those keys live in a ``LiveVerifier`` that is replaced as a
whole when a relocation changes the set. These tests dial the scripted Queen, with a self-signed
certificate for the pinned case.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.agent.link.

Key invariants:
    - A certificate other than the pinned one is refused and no frame is sent.
    - A frame signed by a node the verifier does not know is never yielded.
    - ``open_for_s`` counts only while the connection is open.

See Also:
    - docs/waggle/gateway-contract.md sections 5 and 6.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from device_kit import Device
from queen_double import QueenDouble, QueenSession
from queen_tls import make_tls_material
from states import heartbeat_for

from pollen.agent import Courier, Identity, Link, LiveVerifier
from waggle.clock import Clock
from waggle.errors import (
    ConnectFailedError,
    SignatureError,
    TlsPinMismatchError,
    UnknownSignerError,
)
from waggle.messages.control import Ping
from waggle.outbox import Outbox
from waggle.signing import Ed25519Signer, public_key_from_hex


def _verifier(queen: QueenDouble) -> LiveVerifier:
    """Trust the scripted Queen's Hive node, as an enrolled device does."""
    keys: dict[str, bytes] = {queen.hive_node_id: public_key_from_hex(queen.hive_key_hex)}
    return LiveVerifier(keys)


def _up(link: Link) -> bool:
    """Read whether the link is connected through a call, so mypy does not narrow it."""
    return link.is_connected


@pytest.fixture
async def tls_queen(clock: Clock, tmp_path: Path) -> AsyncIterator[QueenDouble]:
    """A scripted Queen serving ``wss://`` with a self-signed certificate."""
    scripted = QueenDouble(clock, tls=make_tls_material(tmp_path, "listener"))
    await scripted.start()
    try:
        yield scripted
    finally:
        await scripted.stop()


def test_a_verifier_trusts_exactly_the_keys_it_was_given_and_can_be_replaced() -> None:
    first, second = Ed25519Signer.generate(), Ed25519Signer.generate()
    verifier = LiveVerifier({"node_a": first.public_key_bytes})
    canonical = b"frame"
    verifier.verify("node_a", canonical, first.sign(canonical))
    with pytest.raises(UnknownSignerError):
        verifier.verify("node_b", canonical, second.sign(canonical))
    verifier.replace({"node_b": second.public_key_bytes})
    verifier.verify("node_b", canonical, second.sign(canonical))
    with pytest.raises(SignatureError):
        verifier.verify("node_a", canonical, first.sign(canonical))


@pytest.fixture
async def connected(
    queen: QueenDouble, device: Device, clock: Clock
) -> AsyncIterator[tuple[Link, QueenSession]]:
    """A link to the scripted Queen that has announced itself, with the Queen's side of it."""
    link = Link(queen.url, None, device.signer, _verifier(queen), clock)
    await link.connect()
    state = device.state
    courier = Courier(
        Identity(state.device_id, state.node_id, state.hive_id),
        clock,
        Outbox(device.layout.root / "ob.jsonl"),
    )
    await link.send(courier.envelope(heartbeat_for(state), to=state.hive_id))
    session = await queen.next_session()
    try:
        yield link, session
    finally:
        await link.close()


async def test_a_link_knows_how_long_it_has_been_open_and_closes_once(
    connected: tuple[Link, QueenSession],
) -> None:
    link, _ = connected
    assert _up(link)
    await asyncio.sleep(0.05)
    assert link.open_for_s >= 0.04
    await link.close()
    assert not _up(link) and link.open_for_s == 0.0
    await link.close()  # A second close does nothing.


async def test_a_frame_signed_by_the_hive_is_yielded(
    connected: tuple[Link, QueenSession],
) -> None:
    link, session = connected
    await session.send(Ping())
    received = await asyncio.wait_for(anext(link.receive()), timeout=10)
    assert isinstance(received.payload, Ping)


async def test_a_frame_signed_by_a_key_nobody_trusts_is_never_yielded(
    queen: QueenDouble, connected: tuple[Link, QueenSession]
) -> None:
    link, session = connected
    queen.hive_signer = Ed25519Signer.generate()  # Same node id, another key: an impostor.
    await session.send(Ping())
    with pytest.raises(SignatureError):
        await asyncio.wait_for(anext(link.receive()), timeout=10)


async def test_a_wss_link_with_the_right_pin_connects(
    tls_queen: QueenDouble, device: Device, clock: Clock
) -> None:
    link = Link(tls_queen.url, tls_queen.pin, device.signer, _verifier(tls_queen), clock)
    await link.connect()
    try:
        assert _up(link)
    finally:
        await link.close()


async def test_a_certificate_that_is_not_the_pinned_one_is_refused(
    tls_queen: QueenDouble, device: Device, clock: Clock, tmp_path: Path
) -> None:
    other_pin = make_tls_material(tmp_path, "other").sha256
    link = Link(tls_queen.url, other_pin, device.signer, _verifier(tls_queen), clock)
    with pytest.raises(TlsPinMismatchError):
        await link.connect()
    assert tls_queen.sessions == [] or not tls_queen.sessions[-1].frames
    assert not _up(link)


async def test_a_queen_that_is_not_there_is_a_failed_dial(
    device: Device, clock: Clock, queen: QueenDouble
) -> None:
    url = queen.url
    await queen.stop()
    link = Link(url, None, device.signer, _verifier(queen), clock)
    with pytest.raises(ConnectFailedError):
        await link.connect()


def test_the_endpoint_rule_is_held_at_construction(device: Device, clock: Clock) -> None:
    verifier = LiveVerifier({})
    with pytest.raises(ValueError, match=r"loopback|pin"):
        Link("ws://hive.example.net:8720", None, device.signer, verifier, clock)
    with pytest.raises(ValueError, match="pin"):
        Link("ws://127.0.0.1:8720", "ab" * 32, device.signer, verifier, clock)
