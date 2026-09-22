"""Unit tests for hivemind.hive.backends.qemu.qmp: the QMP command sender.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/backends/qemu/qmp.py (codingrules section 3). Roadmap step 5.11 added a
    loopback TCP transport, which works on every platform this suite runs on (unlike the
    Unix-socket half, which this dev host's own asyncio build cannot reach at all, ADR-0026): the
    TCP tests below run a real fake QMP server on `127.0.0.1` and exercise `qmp_execute` against it
    end to end. The Unix-socket transport itself (a real socket, a scripted QMP server) is
    exercised by packages/hivemind/tests/integration/test_qemu_backend.py on a Linux/macOS runner.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.qemu.qmp for qmp_execute, under test.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from hivemind.hive.backends.qemu.qmp import (
    TcpQmpAddress,
    UnixQmpAddress,
    _build_payload,
    qmp_execute,
)
from hivemind.hive.backends.qemu.runner import QemuRunnerError

FakeQmpAddressFactory = Callable[[dict[str, Any]], Awaitable[TcpQmpAddress]]


async def _fake_qmp_server(reply: dict[str, Any]) -> asyncio.Server:
    """Start a loopback TCP server speaking just enough QMP to answer one command with `reply`."""

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b'{"QMP": {"version": {}}}\n')  # The greeting qmp_execute reads and drops.
        await writer.drain()
        await reader.readline()  # qmp_capabilities negotiation request.
        writer.write(b'{"return": {}}\n')
        await writer.drain()
        await reader.readline()  # The actual command under test.
        writer.write((json.dumps(reply) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()

    return await asyncio.start_server(_handle, "127.0.0.1", 0)


@pytest.fixture
async def fake_qmp_address_factory() -> AsyncIterator[FakeQmpAddressFactory]:
    """Yield a callable that starts a fake QMP server for one reply, returning its TcpQmpAddress."""
    servers: list[asyncio.Server] = []

    async def _start(reply: dict[str, Any]) -> TcpQmpAddress:
        server = await _fake_qmp_server(reply)
        servers.append(server)
        sock = server.sockets[0]
        assert sock is not None
        port = sock.getsockname()[1]
        return TcpQmpAddress(host="127.0.0.1", port=port)

    yield _start
    for server in servers:
        server.close()
        await server.wait_closed()


async def test_qmp_execute_over_tcp_succeeds_against_a_fake_server(
    fake_qmp_address_factory: FakeQmpAddressFactory,
) -> None:
    address = await fake_qmp_address_factory({"return": {}})

    await qmp_execute(address, "quit", subject="cell cell_test")  # Must not raise.


async def test_qmp_execute_over_tcp_raises_on_an_error_reply(
    fake_qmp_address_factory: FakeQmpAddressFactory,
) -> None:
    address = await fake_qmp_address_factory({"error": {"desc": "boom"}})

    with pytest.raises(QemuRunnerError, match="boom"):
        await qmp_execute(address, "quit", subject="cell cell_test")


async def test_qmp_execute_over_tcp_is_a_no_op_when_nothing_is_listening() -> None:
    # A port nothing is bound to right now (roadmap step 5.11's own idempotency invariant): a
    # missing endpoint is a silent no-op, the same as a missing Unix socket.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    await qmp_execute(TcpQmpAddress(host="127.0.0.1", port=port), "quit", subject="cell cell_test")


async def test_qmp_execute_raises_when_no_transport_can_reach_a_unix_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Probe-based, not an OS-name check (module docstring): remove the attribute to simulate a
    # host with no Unix-socket QMP support at all, regardless of what platform this suite runs on.
    monkeypatch.delattr(asyncio, "open_unix_connection", raising=False)

    with pytest.raises(QemuRunnerError, match="no Unix-socket"):
        address = UnixQmpAddress(path=tmp_path / "qmp.sock")
        await qmp_execute(address, "quit", subject="cell cell_test")


# ──────────────────────────────────────────────────────────────────────────────
# _build_payload: the wire shape, testable with no socket at all (module docstring).
# ──────────────────────────────────────────────────────────────────────────────


def test_build_payload_omits_arguments_when_none() -> None:
    payload = _build_payload("quit", None)

    assert json.loads(payload) == {"execute": "quit"}
    assert payload.endswith(b"\n")


def test_build_payload_includes_arguments_when_given() -> None:
    payload = _build_payload("human-monitor-command", {"command-line": "savevm snap-1"})

    assert json.loads(payload) == {
        "execute": "human-monitor-command",
        "arguments": {"command-line": "savevm snap-1"},
    }
