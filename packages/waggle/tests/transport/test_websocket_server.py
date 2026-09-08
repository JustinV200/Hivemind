"""Tests for waggle.transport.websocket_server: the listener's lifecycle and properties.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises WebSocketServer on its own: the port
    and uri properties before and after start, idempotent start and close, the end of the
    connections() listing on close, and the shared close-code table in waggle.transport.base.
    What flows over an accepted connection is covered in test_websocket.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.websocket_server for the module under test.
    - waggle.transport.base for close_code_for, pinned here.
"""

from __future__ import annotations

import asyncio

import pytest

from waggle.codec import Codec
from waggle.errors import (
    CodecError,
    FrameTooLargeError,
    InvalidPayloadError,
    InvalidSignatureError,
    MalformedFrameError,
    MissingSignatureError,
    SignatureError,
    UnknownKindError,
    UnknownSignerError,
    UnsupportedVersionError,
)
from waggle.transport.base import (
    CLOSE_CODE_FOR,
    CLOSE_MESSAGE_TOO_BIG,
    CLOSE_POLICY_VIOLATION,
    CLOSE_PROTOCOL_ERROR,
    close_code_for,
)
from waggle.transport.websocket import _format_address
from waggle.transport.websocket_server import DEFAULT_HOST, WebSocketServer

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
IPV6_LOOPBACK = "::1"
IPV6_PORT = 9000
IPV6_FLOW_INFO = 0  # The third element of an IPv6 socket address, never part of a log line.
IPV6_SCOPE_ID = 0  # The fourth element, likewise.


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        (("127.0.0.1", 51497), "127.0.0.1:51497"),
        ((IPV6_LOOPBACK, IPV6_PORT, IPV6_FLOW_INFO, IPV6_SCOPE_ID), f"{IPV6_LOOPBACK}:{IPV6_PORT}"),
        (None, "None"),  # websockets reports None once the socket is gone
        (("only-a-host",), "('only-a-host',)"),
    ],
)
def test_format_address_renders_host_and_port_and_falls_back_to_str(
    address: object, expected: str
) -> None:
    # A private helper, tested directly because no TCP connection ever reports the non-tuple
    # shapes it must still render for a log line.
    assert _format_address(address) == expected


def test_port_and_uri_need_a_started_server(plain_codec: Codec) -> None:
    server = WebSocketServer(plain_codec)

    with pytest.raises(RuntimeError, match="start"):
        _ = server.port
    with pytest.raises(RuntimeError, match="start"):
        _ = server.uri


async def test_start_binds_loopback_on_an_os_assigned_port(plain_codec: Codec) -> None:
    server = WebSocketServer(plain_codec)

    await server.start()
    try:
        assert server.port > 0
        assert server.uri == f"ws://{DEFAULT_HOST}:{server.port}"
    finally:
        await server.close()


async def test_uri_brackets_an_ipv6_host(plain_codec: Codec) -> None:
    server = WebSocketServer(plain_codec, host=IPV6_LOOPBACK)

    await server.start()
    try:
        assert server.uri == f"ws://[{IPV6_LOOPBACK}]:{server.port}"
    finally:
        await server.close()


async def test_start_and_close_are_idempotent_and_close_before_start_is_harmless(
    plain_codec: Codec,
) -> None:
    server = WebSocketServer(plain_codec)
    await server.close()

    await server.start()
    port = server.port
    await server.start()
    assert server.port == port

    async with asyncio.timeout(WAIT_S):
        await server.close()
        await server.close()


async def test_connections_listing_ends_when_the_server_closes(plain_codec: Codec) -> None:
    server = WebSocketServer(plain_codec)
    await server.start()
    listing = asyncio.ensure_future(_list_all(server))
    await asyncio.sleep(0)  # let the listing block on the empty accept queue

    await server.close()

    async with asyncio.timeout(WAIT_S):
        assert await listing == 0


async def _list_all(server: WebSocketServer) -> int:
    """Count the transports connections() yields before it ends."""
    return len([transport async for transport in server.connections()])


# ──────────────────────────────────────────────────────────────────────────────
# The close-code table both transports share
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (MalformedFrameError("x"), CLOSE_PROTOCOL_ERROR),
        (UnsupportedVersionError("x"), CLOSE_PROTOCOL_ERROR),
        (UnknownKindError("x"), CLOSE_PROTOCOL_ERROR),
        (CodecError("x"), CLOSE_PROTOCOL_ERROR),
        (FrameTooLargeError("x"), CLOSE_MESSAGE_TOO_BIG),
        (MissingSignatureError("x"), CLOSE_POLICY_VIOLATION),
        (UnknownSignerError("x"), CLOSE_POLICY_VIOLATION),
        (InvalidSignatureError("x"), CLOSE_POLICY_VIOLATION),
        (SignatureError("x"), CLOSE_POLICY_VIOLATION),
        (InvalidPayloadError("x"), None),
    ],
)
def test_close_code_for_maps_every_decode_failure_as_the_spec_lists(
    error: CodecError | SignatureError, expected: int | None
) -> None:
    assert close_code_for(error) == expected


def test_close_code_table_lists_the_specific_class_before_its_root() -> None:
    # isinstance matching in table order is what makes FrameTooLargeError map to 1009 rather
    # than to its CodecError root's 1002; a reordering would silently change a wire code.
    ordered = list(CLOSE_CODE_FOR)

    assert ordered.index(FrameTooLargeError) < ordered.index(CodecError)
    assert InvalidPayloadError not in CLOSE_CODE_FOR
