"""Unit tests for hivemind.exoskeleton.attach.core and .handle: attach, detach, the trail."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cells import make_capabilities, make_cell, make_identity
from builders.exoskeleton import ScriptedDesktop, StubLauncher, desktop_session, drive

from hivemind.cell.fake import FakeSession, FakeStart
from hivemind.exoskeleton.antennae.xdotool import XdotoolAntennae
from hivemind.exoskeleton.attach import AttachDeps, DisplaySource, attach
from hivemind.exoskeleton.buzz.pulseaudio import PulseAudioBuzz
from hivemind.exoskeleton.compound_eye.x11 import X11CompoundEye
from hivemind.exoskeleton.errors import AttachError
from hivemind.guard import CapabilitySet
from hivemind.pheromone import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.messages.task import ExoskeletonNeed

# A Cell scratch root short enough for the sound server's Unix socket under it on any host;
# pytest's own tmp_path is not (a Windows runner's is well over the 107 bytes a socket allows).
# FakeSession keeps its files in memory, so the directory never has to exist.
_SCRATCH = Path("/lease/scratch")

_GRANTED = CapabilitySet.parse("exoskeleton:display", "exoskeleton:audio", "exoskeleton:browser")
_DESKTOP_CELL = make_capabilities(can_start_display=True, has_audio=True, has_browser=True)


def _deps(clock: FakeClock, launcher: StubLauncher | None = None) -> AttachDeps:
    trail = MemoryPheromoneTrail(clock)
    return AttachDeps(
        trail=trail, identity=make_identity(clock=clock), clock=clock, browser_launcher=launcher
    )


async def _kinds(deps: AttachDeps) -> list[tuple[str, dict[str, object]]]:
    return [(event.kind, dict(event.payload)) for event in await deps.trail.query(TrailQuery())]


async def test_a_desktop_with_audio_and_a_browser_attaches_and_detaches_cleanly() -> None:
    clock = FakeClock()
    session = desktop_session(_SCRATCH, clock)
    launcher = StubLauncher()
    cell = make_cell(capabilities=_DESKTOP_CELL)

    handle = await drive(
        clock, attach(cell, session, ExoskeletonNeed(audio=True), _GRANTED, _deps(clock, launcher))
    )

    peripherals = handle.peripherals
    assert isinstance(peripherals.compound_eye, X11CompoundEye)
    assert isinstance(peripherals.antennae, XdotoolAntennae)
    assert isinstance(peripherals.buzz, PulseAudioBuzz)
    started = [spec.argv[0] for spec in session.started]
    assert started == ["Xvfb", "openbox", "pulseaudio", "chromium"]
    # The browser is headed in the lease display and finds the display and the sound server.
    (request,) = launcher.requests
    assert request.headed
    assert {"DISPLAY", "XAUTHORITY", "PULSE_SERVER", "HOME"} <= set(request.environment)
    report = await handle.detach()
    assert (report.stopped, report.still_running) == (4, 0)
    assert session.running_pids == ()
    assert launcher.browser.closed


async def test_attach_and_detach_are_on_the_trail_as_names_and_counts() -> None:
    clock = FakeClock()
    deps = _deps(clock, StubLauncher())
    cell = make_cell(capabilities=_DESKTOP_CELL)
    need = ExoskeletonNeed(audio=True)

    handle = await drive(
        clock, attach(cell, desktop_session(_SCRATCH, clock), need, _GRANTED, deps)
    )
    await handle.detach()

    peripherals = ["compound_eye", "antennae", "buzz", "browser"]
    assert await _kinds(deps) == [
        (
            "cell.exoskeleton_attached",
            {"peripherals": peripherals, "display": "LEASE", "processes": 4},
        ),
        (
            "cell.exoskeleton_detached",
            {"stopped": 4, "still_running": 0, "peripherals": peripherals},
        ),
    ]


async def test_detach_is_idempotent_and_records_once(tmp_path: Path) -> None:
    clock = FakeClock()
    deps = _deps(clock)
    handle = await drive(
        clock,
        attach(
            make_cell(capabilities=_DESKTOP_CELL),
            desktop_session(tmp_path, clock),
            ExoskeletonNeed(),
            _GRANTED,
            deps,
        ),
    )

    first = await handle.detach()
    second = await handle.detach()

    assert first == second
    assert [kind for kind, _ in await _kinds(deps)].count("cell.exoskeleton_detached") == 1


async def test_a_failure_part_way_stops_everything_already_started() -> None:
    clock = FakeClock()
    session = desktop_session(_SCRATCH, clock)
    session.script_start("pulseaudio", FakeStart(fails="No such file or directory"))
    deps = _deps(clock)

    with pytest.raises(AttachError, match="pulseaudio could not start"):
        await drive(
            clock,
            attach(
                make_cell(capabilities=_DESKTOP_CELL),
                session,
                ExoskeletonNeed(audio=True),
                _GRANTED,
                deps,
            ),
        )

    assert [spec.argv[0] for spec in session.started] == ["Xvfb", "openbox"]
    assert session.running_pids == ()
    assert await _kinds(deps) == []  # Nothing attached, so nothing on the trail.


async def test_a_browser_only_task_starts_the_browser_alone_and_headless(tmp_path: Path) -> None:
    clock = FakeClock()
    session = FakeSession(tmp_path, clock)
    launcher = StubLauncher()
    cell = make_cell(capabilities=make_capabilities(has_browser=True))

    handle = await attach(
        cell, session, ExoskeletonNeed(browser_only=True), _GRANTED, _deps(clock, launcher)
    )

    assert handle.plan.display is DisplaySource.NONE
    assert [spec.argv[0] for spec in session.started] == ["chromium"]
    assert not launcher.requests[0].headed
    assert handle.peripherals.compound_eye is None


async def test_without_a_launcher_a_desktop_attaches_without_the_browser(tmp_path: Path) -> None:
    clock = FakeClock()
    handle = await drive(
        clock,
        attach(
            make_cell(capabilities=_DESKTOP_CELL),
            desktop_session(tmp_path, clock),
            ExoskeletonNeed(),
            _GRANTED,
            _deps(clock),
        ),
    )

    assert not handle.plan.browser
    assert handle.peripherals.browser is None


async def test_without_a_launcher_a_browser_only_task_is_refused_before_anything_starts(
    tmp_path: Path,
) -> None:
    session = FakeSession(tmp_path, FakeClock())
    cell = make_cell(capabilities=make_capabilities(has_browser=True))

    with pytest.raises(AttachError, match="no browser launcher"):
        await attach(
            cell, session, ExoskeletonNeed(browser_only=True), _GRANTED, _deps(FakeClock())
        )

    assert session.started == ()


async def test_the_operators_display_is_borrowed_and_never_stopped(tmp_path: Path) -> None:
    clock = FakeClock()
    session = desktop_session(tmp_path, clock, ScriptedDesktop(running_display=":0"))
    cell = make_cell(capabilities=make_capabilities(has_display=True, real_display_allowed=True))
    granted = CapabilitySet.parse("exoskeleton:real_display")

    handle = await attach(cell, session, ExoskeletonNeed(), granted, _deps(clock))
    report = await handle.detach()

    assert handle.plan.display is DisplaySource.RUNNING
    assert handle.environment["DISPLAY"] == ":0"
    assert session.started == ()  # Nothing was started, so nothing is stopped.
    assert report.stopped == 0
