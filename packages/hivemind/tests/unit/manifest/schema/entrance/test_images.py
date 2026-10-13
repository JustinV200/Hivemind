"""Tests for hivemind.manifest.schema.entrance.images: the image-intake limits under [entrance].

Fits into the Hive:
    Mirrors src/hivemind/manifest/schema/entrance/images.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.manifest.schema.entrance import IMAGE_BYTES_CEILING, EntranceSection

_MIB = 1024 * 1024


def test_the_defaults_take_eight_mebibytes_a_device_may_send_four_in_a_minute() -> None:
    section = EntranceSection()

    assert section.max_image_bytes == 8 * _MIB
    assert section.image_bytes_per_minute == 32 * _MIB
    assert section.image_keep_hours == 24.0


def test_the_ceiling_is_thirty_two_mebibytes() -> None:
    assert IMAGE_BYTES_CEILING == 32 * _MIB


def test_the_limits_load_flat_under_entrance() -> None:
    section = EntranceSection.model_validate(
        {"max_image_bytes": 1_000_000, "image_bytes_per_minute": 2_000_000, "image_keep_hours": 2}
    )

    assert (section.max_image_bytes, section.image_bytes_per_minute) == (1_000_000, 2_000_000)
    assert section.image_keep_hours == 2.0


def test_the_largest_image_may_be_the_ceiling_but_not_a_byte_more() -> None:
    at_the_ceiling = EntranceSection(
        max_image_bytes=IMAGE_BYTES_CEILING, image_bytes_per_minute=IMAGE_BYTES_CEILING
    )

    assert at_the_ceiling.max_image_bytes == IMAGE_BYTES_CEILING
    with pytest.raises(ValidationError, match="max_image_bytes"):
        EntranceSection(
            max_image_bytes=IMAGE_BYTES_CEILING + 1,
            image_bytes_per_minute=2 * IMAGE_BYTES_CEILING,
        )


@pytest.mark.parametrize("field", ["max_image_bytes", "image_bytes_per_minute", "image_keep_hours"])
@pytest.mark.parametrize("value", [0, -1])
def test_a_limit_must_be_positive(field: str, value: int) -> None:
    with pytest.raises(ValidationError, match=field):
        EntranceSection.model_validate({field: value})


def test_the_byte_budget_may_not_be_smaller_than_the_largest_image() -> None:
    with pytest.raises(ValidationError, match="could never fit"):
        EntranceSection(max_image_bytes=2 * _MIB, image_bytes_per_minute=_MIB)
