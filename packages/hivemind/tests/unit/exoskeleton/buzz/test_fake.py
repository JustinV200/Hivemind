"""Unit tests for hivemind.exoskeleton.buzz.fake: FakeBuzz."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.buzz.base import Recording
from hivemind.exoskeleton.buzz.fake import FakeBuzz
from hivemind.exoskeleton.errors import PeripheralError
from waggle.clock import FakeClock


async def test_listen_returns_scripted_recordings_then_silence(tmp_path: Path) -> None:
    buzz = FakeBuzz(FakeSession(tmp_path, FakeClock()))
    scripted = Recording.from_pcm(b"\x10\x00" * 80, 8_000, 1)
    buzz.script(scripted)

    first = await buzz.listen(0.5)
    second = await buzz.listen(0.5)

    assert first == scripted
    assert second.duration_s == 0.5


async def test_say_checks_the_clip_like_the_real_backend_and_remembers_it(tmp_path: Path) -> None:
    session = FakeSession(tmp_path, FakeClock())
    buzz = FakeBuzz(session)
    await session.put_file(Path("a.wav"), Recording.from_pcm(b"\x00\x00", 8_000, 1).wav)

    await buzz.say(Path("a.wav"))
    with pytest.raises(PeripheralError, match="inside scratch"):
        await buzz.say(Path("../b.wav"))

    assert buzz.said == ((tmp_path / "a.wav").resolve(),)


async def test_fail_with_fails_exactly_the_next_call(tmp_path: Path) -> None:
    buzz = FakeBuzz(FakeSession(tmp_path, FakeClock()))
    buzz.fail_with("server gone")

    with pytest.raises(PeripheralError, match="server gone"):
        await buzz.listen(0.5)
    assert (await buzz.listen(0.5)).duration_s == 0.5
