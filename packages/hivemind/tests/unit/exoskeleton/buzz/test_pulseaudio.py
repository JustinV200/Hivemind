"""Unit tests for hivemind.exoskeleton.buzz.pulseaudio: the commands PulseAudioBuzz runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import CompletedCommand, ExecSpec
from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.buzz.base import MAX_LISTEN_S, Recording
from hivemind.exoskeleton.buzz.pulseaudio import LISTEN_RATE, PulseAudioBuzz, PulseServer
from hivemind.exoskeleton.errors import PeripheralError
from waggle.clock import FakeClock

_SERVER = PulseServer(
    socket=Path("/s/pulse/native"), cookie=Path("/s/pulse/cookie"), speaker="spk", microphone="mic"
)


def _buzz(
    tmp_path: Path, seen: list[ExecSpec], code: int = 124
) -> tuple[FakeSession, PulseAudioBuzz]:
    def respond(spec: ExecSpec) -> CompletedCommand:
        seen.append(spec)
        stdout = b"\x00\x00" * LISTEN_RATE if spec.argv[0] == "timeout" else b""
        return CompletedCommand(exit_code=code, stdout=stdout, stderr=b"", duration_s=0.0)

    session = FakeSession(tmp_path, FakeClock(), responder=respond)
    return session, PulseAudioBuzz(session, _SERVER)


def test_the_server_environment_names_its_socket_and_a_cookie_in_scratch() -> None:
    assert _SERVER.environment() == {
        "PULSE_SERVER": "unix:/s/pulse/native",
        "PULSE_COOKIE": "/s/pulse/cookie",
    }


async def test_listen_records_the_speaker_monitor_until_timeout_stops_it(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []
    _, buzz = _buzz(tmp_path, seen)

    recording = await buzz.listen(1.0)

    argv = seen[0].argv
    assert argv[:4] == ("timeout", "--signal=INT", "1.000", "parec")
    assert "--device=spk.monitor" in argv
    assert seen[0].timeout_s > 1.0
    assert (recording.sample_rate, recording.duration_s) == (LISTEN_RATE, 1.0)


async def test_listen_treats_a_real_failure_as_an_error(tmp_path: Path) -> None:
    _, buzz = _buzz(tmp_path, [], code=1)

    with pytest.raises(PeripheralError, match="timeout exited 1"):
        await buzz.listen(1.0)


@pytest.mark.parametrize("seconds", [0.0, 0.05, MAX_LISTEN_S + 1])
async def test_listen_refuses_a_duration_out_of_range(tmp_path: Path, seconds: float) -> None:
    seen: list[ExecSpec] = []
    _, buzz = _buzz(tmp_path, seen)

    with pytest.raises(ValueError, match="listen records"):
        await buzz.listen(seconds)
    assert seen == []


async def test_say_plays_the_checked_clip_into_the_microphone_sink(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []
    session, buzz = _buzz(tmp_path, seen, code=0)
    await session.put_file(Path("hi.wav"), Recording.from_pcm(b"\x00\x00" * 800, 8_000, 1).wav)

    await buzz.say(Path("hi.wav"))

    assert seen[0].argv == ("paplay", "--device=mic", (tmp_path / "hi.wav").resolve().as_posix())
    assert seen[0].env["PULSE_SERVER"] == "unix:/s/pulse/native"
