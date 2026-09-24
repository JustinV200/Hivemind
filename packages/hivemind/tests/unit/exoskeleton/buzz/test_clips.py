"""Unit tests for hivemind.exoskeleton.buzz.clips: the check every Buzz applies before `say`."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.buzz.base import MAX_SAY_S, Recording
from hivemind.exoskeleton.buzz.clips import load_clip
from hivemind.exoskeleton.errors import PeripheralError
from waggle.clock import FakeClock


def _wav(seconds: float, rate: int = 8_000) -> bytes:
    return Recording.from_pcm(b"\x00\x00" * int(seconds * rate), rate, 1).wav


async def test_a_clip_in_scratch_resolves_and_reports_its_length(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.put_file(Path("clips/hi.wav"), _wav(0.5))

    clip = await load_clip(session, Path("clips/hi.wav"))

    assert clip.path == (tmp_path / "clips" / "hi.wav").resolve()
    assert clip.duration_s == 0.5


@pytest.mark.parametrize("path", [Path("../escape.wav"), Path("/etc/passwd")])
async def test_a_clip_outside_scratch_is_refused(tmp_path: Path, path: Path) -> None:
    with pytest.raises(PeripheralError, match="inside scratch"):
        await load_clip(FakeSession(tmp_path, FakeClock()), path)


async def test_a_missing_clip_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PeripheralError, match="no clip"):
        await load_clip(FakeSession(tmp_path, FakeClock()), Path("none.wav"))


async def test_a_file_that_is_not_a_pcm_wav_is_refused(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.put_file(Path("not.wav"), b"ID3 an mp3, say")

    with pytest.raises(PeripheralError, match="not a PCM WAV"):
        await load_clip(session, Path("not.wav"))


async def test_a_clip_longer_than_the_limit_is_refused(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    await session.put_file(Path("long.wav"), _wav(MAX_SAY_S + 1, rate=100))

    with pytest.raises(PeripheralError, match="over"):
        await load_clip(session, Path("long.wav"))
