"""Tests for hivemind.queen.cell_gate.listener's fan-out: backpressure from the Queen to the Cell.

A Virtual Cell's connection is read by one pump that hands the Queen's envelopes to her link's
reader. Its queue used to be unbounded, so while the Queen was not reading, the pump went on
reading the socket into memory and no backpressure ever reached the Cell. The fan-out now holds
at most `FANOUT_QUEUE_SIZE` unread; past that the pump parks and leaves the socket unread. The
"socket" here is a real `MemoryTransport` pair behind a wrapper that counts what the pump reads
off it; waiting is on that count, never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/queen/cell_gate/listener.py (codingrules section 3); split by feature
    (14.2) from test_listener.py, which drives the whole listener over a real loopback WebSocket.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.listener for _FanoutTransport and FANOUT_QUEUE_SIZE.
    - hivemind.queen.inbox.links for the Queen's own per-link reader downstream of it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from builders.supervision import make_telemetry

from hivemind.queen.cell_gate.listener import FANOUT_QUEUE_SIZE, _FanoutTransport
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.errors import ConnectionLostError
from waggle.ids import new_hive_id, new_node_id, new_warden_id
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.transport.memory import MemoryTransport

_POLL_LIMIT = 2_000  # Loop turns a state wait may take before the test fails instead of hanging.
_SETTLE_TURNS = 100  # Loop turns given to a pump that should stay parked through all of them.


class _CountingTransport:
    """The Queen's end of a Cell's link, counting every envelope the fan-out's pump reads off it."""

    def __init__(self, inner: MemoryTransport) -> None:
        """Wrap `inner`; `read` counts what `receive()` has handed out so far."""
        self._inner = inner
        self.read = 0

    @property
    def is_connected(self) -> bool:
        """See `waggle.transport.base.Transport.is_connected`."""
        return self._inner.is_connected

    async def connect(self) -> None:
        """See `waggle.transport.base.Transport.connect`."""
        await self._inner.connect()

    async def send(self, envelope: Envelope) -> None:
        """See `waggle.transport.base.Transport.send`."""
        await self._inner.send(envelope)

    async def receive(self) -> AsyncIterator[Envelope]:
        """See `waggle.transport.base.Transport.receive`; counts every envelope it yields."""
        async for envelope in self._inner.receive():
            self.read += 1
            yield envelope

    async def close(self) -> None:
        """See `waggle.transport.base.Transport.close`."""
        await self._inner.close()


async def _never_dispatched(envelope: Envelope) -> None:
    """The listener's inline answerer; no envelope in these tests is one it answers."""
    raise AssertionError(f"unexpected inline dispatch of {envelope.kind}")


def _heartbeat() -> Heartbeat:
    """One routine Warden Heartbeat: the traffic a Virtual Cell sends most."""
    return Heartbeat(
        telemetry=make_telemetry(),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=15.0,
    )


async def _cell_sends(cell_end: MemoryTransport, count: int) -> list[str]:
    """Send `count` Heartbeats from the Cell's end; return their envelope ids, in order."""
    clock = FakeClock()
    hop = Hop(sender=new_warden_id(clock), recipient=new_hive_id(clock), node_id=new_node_id(clock))
    ids: list[str] = []
    for _ in range(count):
        envelope = wrap(_heartbeat(), hop, clock=clock)
        await cell_end.send(envelope)
        ids.append(envelope.id)
    return ids


async def _until(condition: Callable[[], bool]) -> None:
    """Yield the event loop until `condition()` holds, or fail instead of hanging."""
    for _ in range(_POLL_LIMIT):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")


async def test_the_fan_out_stops_reading_the_socket_while_the_queen_is_not_reading() -> None:
    # Arrange: the Cell sends four fan-outs' worth before the Queen's reader takes a single one.
    queen_end, cell_end = MemoryTransport.pair(Codec(), Codec())
    socket = _CountingTransport(queen_end)
    fanout = _FanoutTransport(socket, _never_dispatched)
    sent = await _cell_sends(cell_end, 4 * FANOUT_QUEUE_SIZE)

    # The pump fills the fan-out, holds one more in hand, and parks: the rest stay in the socket.
    await _until(lambda: socket.read == FANOUT_QUEUE_SIZE + 1)
    for _ in range(_SETTLE_TURNS):
        await asyncio.sleep(0)
    assert socket.read == FANOUT_QUEUE_SIZE + 1

    # Each envelope the Queen takes lets exactly one more off the socket.
    stream = fanout.receive()
    first = await anext(stream)
    await _until(lambda: socket.read == FANOUT_QUEUE_SIZE + 2)
    received = [first, *[await anext(stream) for _ in range(len(sent) - 1)]]

    # Nothing is lost or reordered, and the stream still ends once the Cell hangs up.
    assert [envelope.id for envelope in received] == sent
    await cell_end.close()
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    await asyncio.wait_for(fanout.pump_task, timeout=5.0)


async def test_a_full_fan_out_still_ends_when_the_connection_is_lost() -> None:
    # The Queen is not reading at all when the Cell's link drops: the pump must not park forever
    # waiting for room to say so, and she hears the loss once she drains what was queued.
    queen_end, cell_end = MemoryTransport.pair(Codec(), Codec())
    socket = _CountingTransport(queen_end)
    fanout = _FanoutTransport(socket, _never_dispatched)
    sent = await _cell_sends(cell_end, FANOUT_QUEUE_SIZE)
    await _until(lambda: socket.read == FANOUT_QUEUE_SIZE)

    cell_end.drop()
    await asyncio.wait_for(fanout.pump_task, timeout=5.0)

    stream = fanout.receive()
    received = [await anext(stream) for _ in range(len(sent))]
    assert [envelope.id for envelope in received] == sent
    with pytest.raises(ConnectionLostError):
        await anext(stream)


async def test_a_parked_pump_is_reaped_at_once_when_its_handler_is_cancelled() -> None:
    # CellListener.stop cancels a handler whose Queen stopped reading: the pump, parked on a full
    # fan-out, must end at that cancel rather than park again in its own finally.
    queen_end, cell_end = MemoryTransport.pair(Codec(), Codec())
    socket = _CountingTransport(queen_end)
    fanout = _FanoutTransport(socket, _never_dispatched)
    await _cell_sends(cell_end, 2 * FANOUT_QUEUE_SIZE)
    await _until(lambda: socket.read == FANOUT_QUEUE_SIZE + 1)

    fanout.pump_task.cancel()
    await asyncio.wait_for(asyncio.gather(fanout.pump_task, return_exceptions=True), timeout=5.0)

    assert fanout.pump_task.cancelled()
