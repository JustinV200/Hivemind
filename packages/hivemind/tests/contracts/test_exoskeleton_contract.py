"""Contract suite for the Exoskeleton's peripherals: CompoundEye, Antennae and Buzz.

Every clause runs over the fakes and over the real X11 and PulseAudio backends attached on a real
`LocalProcessSession` (`contracts.exoskeleton_harness`), so a test that passes on the fakes is a
statement about the real peripherals too (roadmap step 6.8). The real harness skips, naming what is
missing, on a host without Xvfb, openbox, xdotool, ImageMagick and PulseAudio.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3). Exercises `hivemind.exoskeleton.compound_eye`,
    `.antennae`, `.buzz` and, for the real harness, `.attach`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - contracts.exoskeleton_harness for the two desktops.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from contracts.exoskeleton_harness import HARNESSES, SCREEN, Desktop, DesktopHarness

from hivemind.exoskeleton.buzz import MAX_LISTEN_S, Recording
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Point, Region
from waggle.messages.capping import MouseButton

_WATCHED = Region(x=20, y=20, width=40, height=30)  # The region the digest clauses watch.
_ELSEWHERE = Region(x=200, y=150, width=50, height=40)  # Far from _WATCHED.
_TONE_RATE = 16_000
_LOUD = 4_000  # A peak above this is the tone, not noise or silence.


@pytest.fixture(params=HARNESSES, ids=lambda factory: factory.name)
async def desktop(request: pytest.FixtureRequest) -> AsyncIterator[Desktop]:
    harness: DesktopHarness = request.param()
    reason = harness.missing()
    if reason is not None:
        pytest.skip(reason)
    opened = await harness.open()
    try:
        yield opened
    finally:
        await harness.close()


def _tone(seconds: float) -> Recording:
    samples = (
        int(12_000 * math.sin(2 * math.pi * 440 * i / _TONE_RATE))
        for i in range(int(seconds * _TONE_RATE))
    )
    return Recording.from_pcm(b"".join(struct.pack("<h", s) for s in samples), _TONE_RATE, 1)


def _peak(recording: Recording) -> int:
    with wave.open(io.BytesIO(recording.wav)) as reader:
        frames = reader.readframes(reader.getnframes())
    return max((abs(s) for (s,) in struct.iter_unpack("<h", frames)), default=0)


# ── CompoundEye ──────────────────────────────────────────────────────────────


async def test_eye_capture_is_a_png_frame_of_the_whole_screen(desktop: Desktop) -> None:
    frame = await desktop.eye.capture()

    assert frame.png.startswith(b"\x89PNG")
    assert (frame.width, frame.height) == (SCREEN.width, SCREEN.height)
    assert desktop.eye.screen == SCREEN


async def test_eye_capture_of_a_region_is_that_regions_size(desktop: Desktop) -> None:
    frame = await desktop.eye.capture(_WATCHED)

    assert (frame.width, frame.height) == (_WATCHED.width, _WATCHED.height)


async def test_eye_digest_is_stable_changes_with_the_region_and_ignores_the_rest(
    desktop: Desktop,
) -> None:
    await desktop.show(_ELSEWHERE, (0, 0, 255))
    baseline = await desktop.eye.region_digest(_WATCHED)

    assert await desktop.eye.region_digest(_WATCHED) == baseline
    await desktop.show(_ELSEWHERE, (0, 255, 0))  # Only outside the watched region changes.
    assert await desktop.eye.region_digest(_WATCHED) == baseline
    await desktop.show(Region(x=30, y=30, width=5, height=5), (255, 0, 0))
    assert await desktop.eye.region_digest(_WATCHED) != baseline


async def test_eye_refuses_a_region_that_leaves_the_screen(desktop: Desktop) -> None:
    beyond = Region(x=SCREEN.width - 10, y=0, width=20, height=10)

    with pytest.raises(PeripheralError, match="does not fit"):
        await desktop.eye.region_digest(beyond)
    with pytest.raises(PeripheralError, match="does not fit"):
        await desktop.eye.capture(beyond)


# ── Antennae ─────────────────────────────────────────────────────────────────


async def test_antennae_move_puts_the_pointer_where_asked(desktop: Desktop) -> None:
    await desktop.antennae.move(Point(33, 44))

    assert await desktop.antennae.pointer() == Point(33, 44)


async def test_antennae_click_leaves_the_pointer_at_the_click(desktop: Desktop) -> None:
    await desktop.antennae.click(Point(50, 60), MouseButton.LEFT, 2)

    assert await desktop.antennae.pointer() == Point(50, 60)


@pytest.mark.parametrize(
    "act",
    [
        lambda a: a.move(Point(SCREEN.width, 0)),
        lambda a: a.click(Point(0, SCREEN.height), MouseButton.LEFT),
        lambda a: a.click(Point(1, 1), MouseButton.RIGHT, 3),
    ],
    ids=["move-off-screen", "click-off-screen", "triple-click"],
)
async def test_antennae_refuse_what_no_display_can_take(
    desktop: Desktop, act: Callable[..., object]
) -> None:
    with pytest.raises(PeripheralError):
        await act(desktop.antennae)  # type: ignore[misc]


async def test_antennae_type_press_and_scroll_complete(desktop: Desktop) -> None:
    await desktop.antennae.type_text("-starts with a dash; ends with a newline\n")
    await desktop.antennae.press("ctrl+a")
    await desktop.antennae.scroll(-1, 2, at=Point(10, 10))

    assert await desktop.antennae.pointer() == Point(10, 10)


# ── Buzz ─────────────────────────────────────────────────────────────────────


async def test_buzz_listen_records_speech_ready_audio_for_about_as_long_as_asked(
    desktop: Desktop,
) -> None:
    recording = await desktop.buzz.listen(1.0)

    assert (recording.sample_rate, recording.channels) == (16_000, 1)
    assert 0.8 <= recording.duration_s <= 1.2


async def test_buzz_listen_hears_what_plays_on_the_speaker(desktop: Desktop) -> None:
    await desktop.play(_tone(2.0))

    heard = await desktop.buzz.listen(1.0)

    assert _peak(heard) > _LOUD


@pytest.mark.parametrize("seconds", [0.0, MAX_LISTEN_S + 1])
async def test_buzz_listen_refuses_a_duration_out_of_range(
    desktop: Desktop, seconds: float
) -> None:
    with pytest.raises(ValueError, match="listen records"):
        await desktop.buzz.listen(seconds)


async def test_buzz_says_a_wav_in_scratch_and_refuses_anything_else(desktop: Desktop) -> None:
    await desktop.session.put_file(Path("hello.wav"), _tone(0.3).wav)
    await desktop.session.put_file(Path("notes.txt"), b"not audio")

    await desktop.buzz.say(Path("hello.wav"))
    with pytest.raises(PeripheralError, match="not a PCM WAV"):
        await desktop.buzz.say(Path("notes.txt"))
    with pytest.raises(PeripheralError, match="inside scratch"):
        await desktop.buzz.say(Path("../elsewhere.wav"))
