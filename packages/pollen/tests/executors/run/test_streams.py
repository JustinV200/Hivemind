"""Test an exec's streams: numbered output chunks, the cap, and stdin fed in order.

A program's standard output and standard error are read concurrently and sent as ``session.output``
chunks, each stream with its own offset counting from 0 and ending with a chunk whose ``final`` is
true, before the exit (gateway-contract section 11). The sum of both streams is held to
``max_output_bytes``: the part that fits is still sent and the group is then killed, so a looping
device can never stream without bound. Stdin goes the other way, written in offset order off the
reader's path, with a bare close honoured whatever its offset. These tests feed real pipes and a
real child, and read what the recording reporter was asked to send.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.run.streams.

Key invariants:
    - Offsets are per stream, contiguous and start at 0; each stream ends exactly once.
    - Not one byte past the cap is sent.

See Also:
    - docs/waggle/gateway-contract.md section 11.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from executor_kit import Limits, RecordingReporter, make_kit, make_plan, open_lease_for

from pollen.executors.run.streams import OutputSink, StdinFeeder
from waggle.clock import FakeClock
from waggle.ids import new_lease_id, new_message_id
from waggle.messages.base import WaggleMessage
from waggle.messages.session import SessionOutput, SessionStdin, SessionStream

CLOCK = FakeClock()
LIMIT_S = 20.0  # A generous bound on a real child, so a hang fails instead of stalling the suite.


async def _sink(tmp_path: Path, cap: int = 1_000_000) -> tuple[OutputSink, RecordingReporter]:
    """Build a sink for a plan with output cap ``cap``, and the reporter it sends through."""
    kit = make_kit(tmp_path, CLOCK)
    lease = await open_lease_for(kit, CLOCK)
    plan = make_plan(kit, CLOCK, lease, ("true",), Limits(max_output_bytes=cap))
    return OutputSink(plan, kit.reporter), kit.reporter


def _reader(*chunks: bytes) -> asyncio.StreamReader:
    """A pipe that delivers ``chunks`` and then ends."""
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


def _stream_of(reporter: RecordingReporter, kind: SessionStream) -> bytes:
    """Check one stream's chunks are contiguous from 0 and end once, and return its bytes."""
    chunks = [c for c in reporter.of(SessionOutput) if c.stream is kind]
    assert [c.final for c in chunks] == [False] * (len(chunks) - 1) + [True]  # One end, last.
    offset = 0
    for chunk in chunks:
        assert chunk.offset == offset  # Each chunk starts where the one before it stopped.
        offset += len(chunk.chunk)
    assert chunks[-1].chunk == b""  # The ending chunk carries no bytes.
    return b"".join(c.chunk for c in chunks)


async def test_each_stream_is_numbered_from_zero_and_ends_with_one_final_chunk(
    tmp_path: Path,
) -> None:
    sink, reporter = await _sink(tmp_path)
    calls: list[int] = []
    await sink.pump(_reader(b"ab", b"cde"), SessionStream.STDOUT, lambda: calls.append(1))
    await sink.pump(_reader(b"xyz"), SessionStream.STDERR, lambda: calls.append(1))
    await sink.end_all()
    await sink.end_all()  # Ending twice sends nothing more.
    assert _stream_of(reporter, SessionStream.STDOUT) == b"abcde"
    assert _stream_of(reporter, SessionStream.STDERR) == b"xyz"
    assert calls == []  # Nothing came near the cap.


async def test_a_pipe_that_does_not_exist_sends_nothing(tmp_path: Path) -> None:
    sink, reporter = await _sink(tmp_path)
    await sink.pump(None, SessionStream.STDOUT, lambda: None)
    assert reporter.sent == []


async def test_output_past_the_cap_is_cut_and_reported_every_time(tmp_path: Path) -> None:
    sink, reporter = await _sink(tmp_path, cap=10)
    calls: list[int] = []
    await sink.pump(
        _reader(b"123456", b"7890ABCDEF"), SessionStream.STDOUT, lambda: calls.append(1)
    )
    await sink.pump(_reader(b"more"), SessionStream.STDERR, lambda: calls.append(1))
    await sink.end_all()
    assert _stream_of(reporter, SessionStream.STDOUT) == b"1234567890"  # Cut exactly at the cap.
    assert _stream_of(reporter, SessionStream.STDERR) == b""  # Nothing more fits.
    assert len(calls) == 2  # The caller is told each time, so it kills the group at once.


async def test_a_chunk_that_fills_the_cap_exactly_has_not_passed_it(tmp_path: Path) -> None:
    sink, reporter = await _sink(tmp_path, cap=4)
    assert await sink.send(SessionStream.STDOUT, b"abcd") is False
    assert await sink.send(SessionStream.STDERR, b"e") is True  # One byte too many.
    assert [c.chunk for c in reporter.of(SessionOutput)] == [b"abcd"]


def _stdin(chunk: bytes, offset: int, *, final: bool = False) -> SessionStdin:
    """Build a stdin event for some exec."""
    return SessionStdin(
        lease_id=new_lease_id(CLOCK),
        exec_id=new_message_id(CLOCK),
        chunk=chunk,
        offset=offset,
        final=final,
        signal=None,
    )


async def _stop(feeder_task: asyncio.Task[None]) -> None:
    """Cancel a feeder and wait for it, so no task outlives the test."""
    feeder_task.cancel()
    await asyncio.gather(feeder_task, return_exceptions=True)


async def test_stdin_is_written_in_order_and_a_misplaced_chunk_is_dropped() -> None:
    feeder = StdinFeeder()
    code = "import sys; sys.stdout.write(sys.stdin.read().upper())"
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    task = asyncio.create_task(feeder.run(child))
    feeder.put(_stdin(b"ab", 0))
    feeder.put(_stdin(b"zz", 99))  # Not at the running length: dropped, not guessed at.
    feeder.put(_stdin(b"cd", 2))
    feeder.put(_stdin(b"", 77, final=True))  # A bare close is honoured at any offset.
    assert child.stdout is not None
    shouted = await asyncio.wait_for(child.stdout.read(), timeout=LIMIT_S)
    await asyncio.wait_for(child.wait(), timeout=LIMIT_S)
    await _stop(task)
    assert shouted == b"ABCD"


async def test_a_program_that_closed_its_stdin_does_not_stop_the_feeder() -> None:
    feeder = StdinFeeder()
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import os; os.close(0)", stdin=subprocess.PIPE
    )
    await asyncio.wait_for(child.wait(), timeout=LIMIT_S)
    task = asyncio.create_task(feeder.run(child))
    feeder.put(_stdin(b"x", 0))
    feeder.put(_stdin(b"y", 1))
    await asyncio.sleep(0.1)  # Real time: the feeder runs on a real pipe whose reader is gone.
    assert not task.done()  # The failed writes were absorbed; it still serves the queue.
    await _stop(task)


async def test_a_program_with_no_stdin_pipe_ignores_what_is_fed() -> None:
    feeder = StdinFeeder()
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "pass", stdin=subprocess.DEVNULL
    )
    task = asyncio.create_task(feeder.run(child))
    feeder.put(_stdin(b"x", 0))
    await asyncio.wait_for(child.wait(), timeout=LIMIT_S)
    await asyncio.sleep(0.05)  # Real time: let the feeder take the event off its queue.
    assert not task.done()
    await _stop(task)


class YieldingReporter(RecordingReporter):
    """A reporter whose send gives the loop a turn, as a real link send does."""

    async def send(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> bool:
        """Record the frame after letting the other pump run."""
        await asyncio.sleep(0)
        return await super().send(payload, to=to, correlation_id=correlation_id)


async def test_two_streams_filling_the_cap_at_once_never_pass_it_together(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, CLOCK)
    lease = await open_lease_for(kit, CLOCK)
    plan = make_plan(kit, CLOCK, lease, ("true",), Limits(max_output_bytes=100))
    reporter = YieldingReporter()
    sink = OutputSink(plan, reporter)
    # Each stream alone fits; both together are 20 bytes over, and each awaits the reporter
    # while the other measures its room.
    passed = await asyncio.gather(
        sink.send(SessionStream.STDOUT, b"o" * 60), sink.send(SessionStream.STDERR, b"e" * 60)
    )
    sent = sum(len(c.chunk) for c in reporter.of(SessionOutput))
    assert sent == 100 and list(passed) == [False, True]
