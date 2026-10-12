"""Test RecordingSession: it forwards every call unchanged and records what was asked.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/runner/session.py
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/runner/session.py for the module under test.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from hivemind.cell.fake import FakeSession
from hivemind.cell.session import BackgroundSpec, CompletedCommand, ExecSpec, run
from hivemind.royal_jelly.quarantine_comb.runner.report import MAX_LIST_ITEMS
from hivemind.royal_jelly.quarantine_comb.runner.session import (
    RecordingSession,
    open_scratch_session,
)
from waggle.clock import FakeClock, SystemClock

_SCRATCH = Path("/scratch")


def _recording() -> RecordingSession:
    """Wrap a fake session that answers every command with success."""
    done = CompletedCommand(exit_code=0, stdout=b"ok", stderr=b"", duration_s=0.0)
    return RecordingSession(FakeSession(_SCRATCH, FakeClock(), responder=lambda spec: done))


def _table(session: RecordingSession) -> dict[tuple[str, str], int]:
    """Return the recorded calls as a mapping."""
    return {(call.op, call.target): call.count for call in session.calls()}


async def test_every_verb_is_recorded_with_its_target_and_forwarded() -> None:
    session = _recording()

    result = await run(session, ExecSpec(argv=("ls", "-l")))
    await session.put_file(Path("out/a.txt"), b"data")
    contents = await session.get_file(Path("out/a.txt"))
    await session.delete_file(Path("out/a.txt"))
    await session.start(BackgroundSpec(argv=("sleep", "1")))

    assert result.stdout == b"ok"
    assert contents == b"data"
    assert _table(session) == {
        ("exec", "ls"): 1,
        ("put_file", "out/a.txt"): 1,
        ("get_file", "out/a.txt"): 1,
        ("delete_file", "out/a.txt"): 1,
        ("start", "sleep"): 1,
    }


async def test_a_repeated_call_raises_its_count_not_the_table_size() -> None:
    session = _recording()

    for _ in range(3):
        await run(session, ExecSpec(argv=("ls",)))

    assert _table(session) == {("exec", "ls"): 3}


async def test_paths_are_recorded_relative_to_scratch_when_inside_it() -> None:
    session = _recording()
    await session.put_file(_SCRATCH / "deep" / "x.txt", b"1")

    await session.put_file(Path("./y/../z.txt"), b"2")

    assert set(_table(session)) == {("put_file", "deep/x.txt"), ("put_file", "z.txt")}


async def test_a_refused_call_is_still_on_the_record() -> None:
    session = _recording()

    # The fake refuses a path outside scratch; that is the point, so the error is suppressed.
    with contextlib.suppress(Exception):
        await session.get_file(Path("/etc/passwd"))

    assert ("get_file", "/etc/passwd") in _table(session)


async def test_the_table_stops_growing_at_its_bound_but_keeps_counting_known_calls() -> None:
    session = _recording()
    for index in range(MAX_LIST_ITEMS + 10):
        await run(session, ExecSpec(argv=(f"cmd{index}",)))

    await run(session, ExecSpec(argv=("cmd0",)))

    assert len(session.calls()) == MAX_LIST_ITEMS
    assert _table(session)[("exec", "cmd0")] == 2


async def test_a_real_scratch_session_runs_commands_and_files(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    session = RecordingSession(await open_scratch_session(scratch, SystemClock()))

    await session.put_file(Path("note.txt"), b"hello")
    listing = await run(session, ExecSpec(argv=("ls",)))

    assert session.scratch_dir == scratch
    assert listing.stdout.split() == [b"note.txt"]
    assert await session.get_file(Path("note.txt")) == b"hello"
    await session.close()
    assert not session.is_open
