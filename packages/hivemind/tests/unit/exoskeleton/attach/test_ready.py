"""Unit tests for hivemind.exoskeleton.attach.ready: Deadline, start_process and stop_all."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import BackgroundSpec
from hivemind.cell.fake import FakeSession, FakeStart
from hivemind.exoskeleton.attach.ready import POLL_INTERVAL_S, Deadline, start_process, stop_all
from hivemind.exoskeleton.errors import AttachError
from waggle.clock import FakeClock


def test_a_deadline_expires_once_the_clock_passes_its_budget() -> None:
    clock = FakeClock()
    deadline = Deadline.after(clock, 1.0)

    assert not deadline.expired()
    clock.advance(1.0)
    assert deadline.expired()


async def test_a_program_the_cell_lacks_is_an_attach_error(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    session.script_start("Xvfb", FakeStart(fails="No such file or directory"))

    with pytest.raises(AttachError, match="Xvfb could not start on this Cell"):
        await start_process(session, BackgroundSpec(argv=("Xvfb",)))


async def test_stop_all_stops_newest_first_and_counts_only_what_was_running(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    session.script_start("crashed", FakeStart(running=False))
    first = await session.start(BackgroundSpec(argv=("display",)))
    second = await session.start(BackgroundSpec(argv=("crashed",)))
    third = await session.start(BackgroundSpec(argv=("browser",)))

    stopped = await stop_all(session, [first, second, third])

    assert stopped == 2
    assert session.running_pids == ()
    assert POLL_INTERVAL_S > 0
    assert not await session.is_running(third)
