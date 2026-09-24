"""Unit tests for hivemind.exoskeleton.attach.display: a lease display, or a borrowed one."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.exoskeleton import DISPLAY_NUMBER, ScriptedDesktop, desktop_session, drive

from hivemind.cell.fake import FakeStart
from hivemind.cell.local.probe import WINDOW_MANAGERS, X11_DISPLAY_PROGRAMS
from hivemind.exoskeleton.attach.display import (
    WINDOW_MANAGER,
    X11_PROGRAMS,
    borrow_running_display,
    start_lease_display,
)
from hivemind.exoskeleton.attach.ready import Deadline
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.geometry import ScreenSize
from hivemind.exoskeleton.scratch import ScratchLayout
from waggle.clock import FakeClock

_SCREEN = ScreenSize(800, 600)


def test_attach_runs_exactly_the_programs_the_probe_requires() -> None:
    # The probe reports can_start_display only when these are on PATH; attach must need no more.
    assert set(X11_PROGRAMS) == set(X11_DISPLAY_PROGRAMS)
    assert (WINDOW_MANAGER,) == WINDOW_MANAGERS


async def test_a_lease_display_is_private_and_ready_for_input(tmp_path: Path) -> None:
    clock = FakeClock()
    session = desktop_session(tmp_path, clock)
    layout = ScratchLayout.under(tmp_path)

    started = await drive(
        clock, start_lease_display(session, layout, _SCREEN, Deadline.after(clock, 5))
    )

    assert started.display.name == f":{DISPLAY_NUMBER}"
    assert started.display.authority == layout.authority
    xvfb, wm = session.started
    assert xvfb.argv[0] == "Xvfb"
    assert xvfb.argv[6:8] == ("-nolisten", "tcp")
    assert "-auth" in xvfb.argv
    assert wm.argv == ("openbox", "--sm-disable")
    assert wm.env["HOME"] == str(layout.home)
    assert wm.env["DISPLAY"] == f":{DISPLAY_NUMBER}"
    assert len(await session.get_file(layout.authority)) == 44  # One wildcard cookie entry.


async def test_an_xvfb_that_dies_is_reported_with_its_log(tmp_path: Path) -> None:
    clock = FakeClock()
    session = desktop_session(tmp_path, clock)
    session.script_start("Xvfb", FakeStart(log=b"Fatal server error: no screens", running=False))

    with pytest.raises(AttachError, match="exited before it was ready: Fatal server error"):
        await drive(
            clock,
            start_lease_display(
                session, ScratchLayout.under(tmp_path), _SCREEN, Deadline.after(clock, 5)
            ),
        )

    assert session.running_pids == ()


async def test_a_display_that_ignores_the_pointer_times_out_and_leaves_nothing_running(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    session = desktop_session(tmp_path, clock, ScriptedDesktop(pointer_moves=False))

    with pytest.raises(AttachError, match="did not take pointer input within 2"):
        await drive(
            clock,
            start_lease_display(
                session, ScratchLayout.under(tmp_path), _SCREEN, Deadline.after(clock, 2)
            ),
        )

    assert len(session.started) == 2
    assert session.running_pids == ()


async def test_a_missing_window_manager_stops_the_display_already_started(tmp_path: Path) -> None:
    clock = FakeClock()
    session = desktop_session(tmp_path, clock)
    session.script_start("openbox", FakeStart(fails="No such file or directory"))

    with pytest.raises(AttachError, match="openbox could not start"):
        await drive(
            clock,
            start_lease_display(
                session, ScratchLayout.under(tmp_path), _SCREEN, Deadline.after(clock, 5)
            ),
        )

    assert session.running_pids == ()


async def test_borrowing_reads_the_inherited_display_and_its_size(tmp_path: Path) -> None:
    session = desktop_session(tmp_path, FakeClock(), ScriptedDesktop(running_display=":0"))

    borrowed = await borrow_running_display(session)

    assert borrowed.display.name == ":0"
    assert borrowed.display.authority is None  # The session's own XAUTHORITY applies.
    assert borrowed.display.size == ScreenSize(1920, 1080)
    assert borrowed.processes == ()


@pytest.mark.parametrize("shown", [None, "localhost:10.0"])
async def test_borrowing_refuses_anything_but_a_local_display(
    tmp_path: Path, shown: str | None
) -> None:
    session = desktop_session(tmp_path, FakeClock(), ScriptedDesktop(running_display=shown))

    with pytest.raises(AttachError, match="not a local X display"):
        await borrow_running_display(session)
