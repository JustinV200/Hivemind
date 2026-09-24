"""Unit tests for hivemind.exoskeleton.attach.audio: the lease's own sound server."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.exoskeleton import ScriptedDesktop, desktop_session, drive

from hivemind.cell.fake import FakeStart
from hivemind.cell.local.probe import AUDIO_PROGRAMS as PROBED
from hivemind.exoskeleton.attach.audio import (
    AUDIO_PROGRAMS,
    MICROPHONE_SINK,
    SPEAKER_SINK,
    server_script,
    start_sound_server,
)
from hivemind.exoskeleton.attach.ready import Deadline
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.scratch import MAX_SOCKET_PATH_BYTES, ScratchLayout
from waggle.clock import FakeClock


def test_attach_runs_exactly_the_audio_programs_the_probe_requires() -> None:
    assert set(AUDIO_PROGRAMS) == set(PROBED)


def test_the_script_loads_two_rewindless_sinks_and_sets_both_defaults() -> None:
    layout = ScratchLayout.under(Path("/s"))

    script = server_script(layout)

    assert f'socket="{layout.pulse_socket}"' in script
    assert script.count("norewinds=1") == 2
    assert f"set-default-sink {SPEAKER_SINK}" in script
    assert f"set-default-source {MICROPHONE_SINK}.monitor" in script


def test_a_scratch_too_deep_for_a_socket_is_refused_before_anything_runs() -> None:
    layout = ScratchLayout.under(Path("/" + "d" * MAX_SOCKET_PATH_BYTES))

    with pytest.raises(AttachError, match="shorter scratch root"):
        server_script(layout)


def test_a_scratch_path_the_script_cannot_quote_is_refused() -> None:
    with pytest.raises(AttachError, match="cannot quote"):
        server_script(ScratchLayout.under(Path('/s/"q')))


async def test_the_server_is_ready_once_it_answers_on_its_own_socket(tmp_path: Path) -> None:
    clock = FakeClock()
    desktop = ScriptedDesktop(sound_ready_after=3)
    session = desktop_session(tmp_path, clock, desktop)
    layout = ScratchLayout.under(tmp_path)

    started = await drive(clock, start_sound_server(session, layout, Deadline.after(clock, 5)))

    assert desktop.programs().count("pactl") == 4
    (spec,) = session.started
    assert spec.argv[:3] == ("pulseaudio", "-n", "-F")
    assert "--disallow-module-loading" in spec.argv
    assert spec.env["PULSE_SERVER"] == f"unix:{layout.pulse_socket}"
    assert spec.env["HOME"] == str(layout.home)
    assert started.server.speaker == SPEAKER_SINK


async def test_a_server_that_exits_is_reported_and_stopped(tmp_path: Path) -> None:
    clock = FakeClock()
    session = desktop_session(tmp_path, clock, ScriptedDesktop(sound_ready_after=99))
    session.script_start("pulseaudio", FakeStart(log=b"E: Failed to load module", running=False))

    with pytest.raises(AttachError, match="exited before it was ready: E: Failed to load module"):
        await drive(
            clock,
            start_sound_server(session, ScratchLayout.under(tmp_path), Deadline.after(clock, 5)),
        )

    assert session.running_pids == ()
