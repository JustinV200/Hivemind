"""Tests for hivemind.wardens.offline.link over a real WebSocket that goes away and comes back.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/link.py (codingrules section 3), split by feature (14.2)
    from test_link.py, which scripts the Queen in memory. Here the Queen is a real
    `waggle.transport.websocket_server.WebSocketServer` on a loopback port and the link dials it
    with the real `WebSocketClientTransport`: the server is shut down, the Warden sends while it
    is gone, and the server is started again on the same port. What the Queen then hears is the
    acceptance test of roadmap 11.9's link: a new announcement, the queued messages in order, the
    held question last, nothing twice, the phases in order, and a reader that never spun.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.link for the module under test.
    - waggle.transport.websocket_server for the listener standing in for the Queen.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.offline import AutoClock, LinkRig

from hivemind.common.tasks import reap_all
from hivemind.wardens.offline import LinkPhase, OfflineRefusedError
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import TransportError
from waggle.transport.base import Transport
from waggle.transport.websocket_client import DialOptions, WebSocketClientTransport
from waggle.transport.websocket_server import WebSocketServer

_REDIAL_PACE_S = 0.01  # Real time between redials, so a closed port is not hammered.
_DIAL_TIMEOUT_S = 2.0  # One real dial: loopback answers or refuses in milliseconds.


class _PacedClock(AutoClock):
    """An AutoClock that also lets a little real time pass, so a refused dial is not a hot loop."""

    async def sleep(self, seconds: float) -> None:
        await super().sleep(seconds)
        await asyncio.sleep(_REDIAL_PACE_S)


class _SocketQueen:
    """A real listener standing in for the Queen: keeps every frame, goes away, comes back."""

    def __init__(self, activity: asyncio.Event) -> None:
        self._codec = Codec()
        self._activity = activity
        self._server: WebSocketServer | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.received: list[Envelope] = []
        self.connections = 0
        self.uri = ""

    async def start(self, port: int = 0) -> None:
        """Listen on `port` (an OS-assigned one at first), accepting and reading connections."""
        server = WebSocketServer(self._codec, port=port)
        await server.start()
        self._server = server
        self.uri = server.uri
        self._tasks.append(asyncio.ensure_future(self._accept(server)))

    async def go_away(self) -> None:
        """Shut the listener and every connection, as a Queen that restarted does."""
        assert self._server is not None
        await self._server.close()
        self._server = None
        await reap_all(self._tasks)
        self._tasks.clear()

    async def stop(self) -> None:
        """Go away if still listening; the end of every test."""
        if self._server is not None:
            await self.go_away()

    async def come_back(self) -> None:
        """Listen again on the port the Queen had before."""
        await self.start(port=int(self.uri.rsplit(":", 1)[1]))

    async def _accept(self, server: WebSocketServer) -> None:
        readers: list[asyncio.Task[None]] = []
        try:
            async for transport in server.connections():
                self.connections += 1
                self._activity.set()
                readers.append(asyncio.ensure_future(self._read(transport)))
        finally:
            await reap_all(readers)

    async def _read(self, transport: Transport) -> None:
        try:
            async for envelope in transport.receive():
                self.received.append(envelope)
                self._activity.set()
        except TransportError:
            return  # The connection broke: what had arrived is kept.


class _Dialer:
    """Dials the Queen's listener with the real client, one attempt per call."""

    def __init__(self, clock: AutoClock) -> None:
        self.queen: _SocketQueen | None = None  # Set once the rig, which the queen wakes, exists.
        self._clock = clock

    async def dial(self) -> Transport:
        assert self.queen is not None
        client = WebSocketClientTransport(
            self.queen.uri,
            Codec(),
            self._clock,
            options=DialOptions(max_attempts=1, open_timeout_s=_DIAL_TIMEOUT_S),
        )
        await client.connect()
        return client


@pytest.fixture
async def socket_rig(tmp_path: Path) -> AsyncIterator[tuple[LinkRig, _SocketQueen]]:
    """A link dialling a real listener, already pumping; everything is closed after the test."""
    clock = _PacedClock()
    dialer = _Dialer(clock)
    rig = LinkRig(tmp_path, dialer=dialer, clock=clock)
    queen = _SocketQueen(rig.changed)
    dialer.queen = queen
    await queen.start()
    rig.start()
    yield rig, queen
    await rig.link.close()
    await queen.stop()
    await rig.stop()


async def _send_while_away(rig: LinkRig) -> dict[str, Envelope]:
    """Send the Warden's usual messages into the outage; return those that must come back."""
    wire = rig.wire
    sent = {
        "result": wire.result(),
        "alarm": wire.alarm(),
        "question": wire.question(),
        "checkpoint": wire.progress(),
        "request": wire.forage_request(),
    }
    for envelope in sent.values():
        await rig.link.send(envelope)
    await rig.link.send(wire.heartbeat())  # Dropped: stale by the time the link is back.
    with pytest.raises(OfflineRefusedError):
        await rig.link.send(wire.cell_request())  # Refused: an offline Warden cannot grow.
    return sent


async def test_a_queen_that_goes_away_and_comes_back_is_caught_up_in_order_with_no_duplicate(
    socket_rig: tuple[LinkRig, _SocketQueen],
) -> None:
    rig, queen = socket_rig
    await rig.until(lambda: rig.link.is_online and len(queen.received) == 1)
    live = rig.wire.result()
    await rig.link.send(live)  # Sent while up: it must arrive once and not again.
    await rig.until(lambda: len(queen.received) == 2)
    rig.phases.clear()

    await queen.go_away()
    await rig.until(lambda: not rig.link.is_online)
    sent = await _send_while_away(rig)
    await asyncio.sleep(0.1)  # Several redials against a closed port.
    assert rig.pump_is_running  # The reader waited through the outage; it never ended.
    assert rig.outbox.queued() == 4 and rig.outbox.held() == 1

    await queen.come_back()
    await rig.until(lambda: rig.link.is_online and len(queen.received) == 2 + 1 + 5)

    # The announcement first, then the queue in the order it was written (the request in a fresh
    # envelope), then the question held for the Queen; the live message is not heard twice.
    heard = queen.received[2:]
    assert [e.kind for e in heard] == [
        "cell.heartbeat",
        "task.result",
        "supervision.alarm_raised",
        "task.progress",
        "forage.request",
        "supervision.question",
    ]
    assert [e.id for e in heard[1:4]] == [sent[k].id for k in ("result", "alarm", "checkpoint")]
    assert heard[4].payload == sent["request"].payload and heard[4].id != sent["request"].id
    assert heard[5].id == sent["question"].id
    ids = [e.id for e in queen.received if e.kind != "cell.heartbeat"]
    assert len(ids) == len(set(ids)) and live.id in ids
    assert rig.phases[0] is LinkPhase.OFFLINE
    assert rig.phases[-2:] == [LinkPhase.RECOVERING, LinkPhase.ONLINE]
    assert queen.connections == 2 and len(rig.after.outages) == 2  # The trail step ran twice.
    assert rig.outbox.queued() == 0 and rig.outbox.held() == 0


async def test_the_reader_keeps_yielding_the_queens_frames_after_a_reconnection(
    socket_rig: tuple[LinkRig, _SocketQueen],
) -> None:
    rig, queen = socket_rig
    await rig.until(lambda: rig.link.is_online)

    await queen.go_away()
    await rig.until(lambda: not rig.link.is_online)
    await queen.come_back()
    await rig.until(lambda: rig.link.is_online and queen.connections == 2)

    assert rig.pump_is_running  # Still the one reader, on the new connection.
