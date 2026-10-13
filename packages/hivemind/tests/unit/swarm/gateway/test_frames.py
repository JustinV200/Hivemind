"""Tests for hivemind.swarm.gateway.frames: one connection's single reader, sender and pin.

Fits into the Hive:
    Mirrors src/hivemind/swarm/gateway/frames.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.gateway.frames for the module under test.
    - docs/waggle/spec.md section 7 for the invalid-payload rule.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
import structlog
from builders.swarm import make_device_party, make_hive_party

from hivemind.swarm.errors import DeviceTimeoutError, GatewayUnavailableError
from hivemind.swarm.gateway import frames as frames_module
from hivemind.swarm.gateway.frames import (
    FrameSender,
    HiveVoice,
    LinkEnds,
    next_frame,
    open_connection,
    pinned,
)
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import NodeId, new_message_id, new_node_id
from waggle.messages.control import ErrorMessage
from waggle.messages.swarm import DeviceHeartbeat, RuntimeLevel
from waggle.signing import Ed25519Signer, Ed25519Verifier
from waggle.transport.memory import MemoryTransport

_CLOCK = FakeClock()
_HIVE = make_hive_party(_CLOCK)
_DEVICE = make_device_party(_CLOCK)
_VOICE = HiveVoice(_HIVE.hive_id, _HIVE.node_id)


def _beat() -> DeviceHeartbeat:
    """A valid heartbeat."""
    return DeviceHeartbeat(
        device_id=_DEVICE.device_id,
        level=RuntimeLevel.GATEWAY,
        lease_ids=(),
        hive_started_processes=0,
        outbox_pending=0,
        uptime_s=1.0,
    )


def _from_device(node_id: NodeId | None = None) -> Envelope:
    """A heartbeat envelope from the device, signed as `node_id` when given."""
    hop = Hop(
        sender=_DEVICE.device_id,
        recipient=_HIVE.hive_id,
        node_id=node_id if node_id is not None else _DEVICE.node_id,
    )
    return wrap(_beat(), hop, clock=_CLOCK)


def _pair() -> tuple[MemoryTransport, MemoryTransport]:
    """An unsigned in-memory pair: (the Hive's end, the device's end)."""
    return MemoryTransport.pair(Codec(), Codec())


async def test_next_frame_returns_the_first_frame() -> None:
    hive_end, device_end = _pair()
    connection = open_connection(hive_end, _VOICE, _CLOCK)
    sent = _from_device()
    await device_end.send(sent)

    assert await next_frame(connection, 1.0) == sent


async def test_next_frame_is_none_when_nothing_arrives_in_time() -> None:
    hive_end, _device_end = _pair()

    assert await next_frame(open_connection(hive_end, _VOICE, _CLOCK), 0.05) is None


async def test_next_frame_is_none_when_the_peer_closes() -> None:
    hive_end, device_end = _pair()
    connection = open_connection(hive_end, _VOICE, _CLOCK)

    await device_end.close()

    assert await next_frame(connection, 1.0) is None


async def test_next_frame_is_none_when_a_frame_fails_its_signature() -> None:
    signer = Ed25519Signer.generate()
    hive_end, device_end = MemoryTransport.pair(
        Codec(verifier=Ed25519Verifier({})), Codec(signer=signer)
    )
    connection = open_connection(hive_end, _VOICE, _CLOCK)
    await device_end.send(_from_device())

    assert await next_frame(connection, 1.0) is None


async def test_an_invalid_payload_is_answered_with_a_control_error_and_the_connection_goes_on() -> (
    None
):
    hive_end, device_end = _pair()
    connection = open_connection(hive_end, _VOICE, _CLOCK)
    wire = json.loads(Codec().encode(_from_device()))
    wire["payload"]["uptime_s"] = -1  # A valid envelope around a payload the model refuses.
    bad_id = wire["id"]
    hive_end.inject_frame(json.dumps(wire).encode())
    good = _from_device()
    await device_end.send(good)

    assert await next_frame(connection, 1.0) == good
    answered = await anext(device_end.receive())
    assert isinstance(answered.payload, ErrorMessage)
    assert answered.correlation_id == bad_id
    assert answered.sender == _HIVE.hive_id


async def test_an_invalid_payload_whose_sender_is_not_an_address_cannot_be_answered() -> None:
    hive_end, device_end = _pair()
    connection = open_connection(hive_end, _VOICE, _CLOCK)
    wire = json.loads(Codec().encode(_from_device()))
    wire["payload"]["uptime_s"] = -1
    wire["sender"] = "not an address"
    hive_end.inject_frame(json.dumps(wire).encode())
    good = _from_device()
    await device_end.send(good)

    assert await next_frame(connection, 1.0) == good


async def test_pinned_ends_the_stream_at_the_first_frame_from_another_node() -> None:
    hive_end, device_end = _pair()
    connection = pinned(open_connection(hive_end, _VOICE, _CLOCK), _DEVICE.node_id)
    own, stranger = _from_device(), _from_device(node_id=new_node_id(_CLOCK))
    await device_end.send(own)
    await device_end.send(stranger)
    await device_end.send(_from_device())

    with structlog.testing.capture_logs() as logs:
        received = [envelope async for envelope in connection.frames]

    assert received == [own]
    assert [entry["event"] for entry in logs] == ["swarm.node_pin_violated"]


async def test_pinned_passes_every_frame_of_the_pinned_node_and_ends_with_the_connection() -> None:
    hive_end, device_end = _pair()
    connection = pinned(open_connection(hive_end, _VOICE, _CLOCK), _DEVICE.node_id)
    frames = [_from_device(), _from_device()]
    for frame in frames:
        await device_end.send(frame)
    await device_end.close()

    assert [envelope async for envelope in connection.frames] == frames


async def test_a_sent_frame_is_addressed_from_the_hive_to_the_device() -> None:
    hive_end, device_end = _pair()
    sender = FrameSender(hive_end, LinkEnds(_VOICE, _DEVICE.device_id), _CLOCK)
    message_id = new_message_id(_CLOCK)

    sent = await sender.send(
        _beat(), sender=_HIVE.hive_id, correlation_id=None, message_id=message_id
    )

    arrived = await anext(device_end.receive())
    assert sent == message_id
    assert (arrived.id, arrived.sender, arrived.recipient, arrived.node_id) == (
        message_id,
        _HIVE.hive_id,
        _DEVICE.device_id,
        _HIVE.node_id,
    )


async def test_sending_on_a_closed_transport_raises_gateway_unavailable() -> None:
    hive_end, device_end = _pair()
    sender = FrameSender(hive_end, LinkEnds(_VOICE, _DEVICE.device_id), _CLOCK)
    await device_end.close()

    with pytest.raises(GatewayUnavailableError):
        await sender.send(
            _beat(), sender=_HIVE.hive_id, correlation_id=None, message_id=new_message_id(_CLOCK)
        )


class _StalledTransport:
    """A transport whose send never completes: a peer whose socket stopped taking data."""

    is_connected = True

    async def connect(self) -> None:
        return None

    async def send(self, envelope: Envelope) -> None:
        await asyncio.sleep(3600)

    def receive(self) -> AsyncIterator[Envelope]:
        raise NotImplementedError("a stalled transport is never read")

    async def close(self) -> None:
        return None


async def test_a_send_the_transport_cannot_take_in_time_raises_device_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frames_module, "SEND_TIMEOUT_S", 0.05)
    sender = FrameSender(_StalledTransport(), LinkEnds(_VOICE, _DEVICE.device_id), _CLOCK)

    with pytest.raises(DeviceTimeoutError):
        await sender.send(
            _beat(), sender=_HIVE.hive_id, correlation_id=None, message_id=new_message_id(_CLOCK)
        )
