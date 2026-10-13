"""Tests for hivemind.entrance.voice.nectar: kept clips are C2, bounded, and swept on time.

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/nectar.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from hivemind.cell import HoneyClearance
from hivemind.entrance.voice import InMemoryMediaNectar, KeptAudio, KeptImage
from hivemind.llm.transcription import AudioMediaType
from waggle.clock import FakeClock
from waggle.ids import new_device_id, new_nectar_id

_CLOCK = FakeClock()
_HOUR = timedelta(hours=1)


def _kept(ref: str, size: int = 64, hours: float = 1.0) -> KeptAudio:
    """A kept clip of ``size`` bytes, kept now, expiring in ``hours``."""
    now = _CLOCK.now()
    return KeptAudio(
        ref=ref,
        device_id=new_device_id(_CLOCK),
        media_type=AudioMediaType.WAV,
        duration_s=1.0,
        data=b"\x01" * size,
        kept_at=now,
        expires_at=now + hours * _HOUR,
    )


def _image(size: int = 64, hours: float = 1.0) -> KeptImage:
    """A kept image of ``size`` bytes, kept now, expiring in ``hours``."""
    now = _CLOCK.now()
    return KeptImage(
        ref=new_nectar_id(_CLOCK),
        device_id=new_device_id(_CLOCK),
        media_type="image/png",
        data=b"\x02" * size,
        caption="the back fence",
        task_id=None,
        kept_at=now,
        expires_at=now + hours * _HOUR,
    )


async def test_a_kept_clip_stays_until_its_window_passes_then_the_sweep_takes_it() -> None:
    nectar = InMemoryMediaNectar()
    await nectar.deposit(_kept("goalreq_short", hours=1.0))
    await nectar.deposit(_kept("goalreq_long", hours=3.0))

    before = await nectar.sweep(_CLOCK.now() + 0.5 * _HOUR)
    after = await nectar.sweep(_CLOCK.now() + 2 * _HOUR)

    assert (before, after) == (0, 1)
    assert [kept.ref for kept in nectar.kept] == ["goalreq_long"]
    assert nectar.kept[0].clearance is HoneyClearance.C2


async def test_the_store_is_bounded_by_count_and_bytes_oldest_first() -> None:
    by_count = InMemoryMediaNectar(max_clips=2)
    by_bytes = InMemoryMediaNectar(max_bytes=100)

    for ref in ("a", "b", "c"):
        await by_count.deposit(_kept(ref))
        await by_bytes.deposit(_kept(ref, size=40))

    assert [kept.ref for kept in by_count.kept] == ["b", "c"]
    assert [kept.ref for kept in by_bytes.kept] == ["b", "c"]


def test_a_kept_clip_is_always_c2_and_expires_after_it_was_kept() -> None:
    kept = _kept("goalreq_x")

    with pytest.raises(ValueError, match="C2"):
        dataclasses.replace(kept, clearance=HoneyClearance.C1)
    with pytest.raises(ValueError, match="expires after"):
        dataclasses.replace(kept, expires_at=kept.kept_at)
    assert "data=" not in repr(kept)  # The audio never reaches a repr, so never a log line.


def test_bounds_below_one_are_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        InMemoryMediaNectar(max_clips=0)


async def test_clips_and_images_share_one_store_one_sweep_and_one_set_of_bounds() -> None:
    nectar = InMemoryMediaNectar(max_clips=3)
    await nectar.deposit(_kept("goalreq_clip", hours=1.0))
    await nectar.deposit(_image(hours=3.0))
    await nectar.deposit(_image(hours=1.0))
    await nectar.deposit(_image(hours=3.0))  # Past the count of three: the oldest, the clip, goes.

    swept = await nectar.sweep(_CLOCK.now() + 2 * _HOUR)

    assert swept == 1
    assert [type(kept) for kept in nectar.kept] == [KeptImage, KeptImage]


async def test_images_count_toward_the_byte_bound() -> None:
    nectar = InMemoryMediaNectar(max_bytes=100)

    for _ in range(3):
        await nectar.deposit(_image(size=40))

    assert [len(kept.data) for kept in nectar.kept] == [40, 40]


def test_a_kept_image_is_always_c2_and_expires_after_it_was_kept() -> None:
    kept = _image()

    with pytest.raises(ValueError, match="C2"):
        dataclasses.replace(kept, clearance=HoneyClearance.C1)
    with pytest.raises(ValueError, match="expires after"):
        dataclasses.replace(kept, expires_at=kept.kept_at)
    assert kept.clearance is HoneyClearance.C2 and kept.size == 64


def test_a_kept_images_bytes_and_caption_never_reach_a_repr() -> None:
    shown = repr(_image())

    assert "data=" not in shown
    assert "back fence" not in shown
