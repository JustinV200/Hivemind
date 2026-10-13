"""Tests for hivemind.entrance.images.formats: the three image formats and their signatures.

Fits into the Hive:
    Mirrors src/hivemind/entrance/images/formats.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from builders.entrance.images import jpeg, png, webp

from hivemind.entrance.images import ImageMediaType, looks_like, media_type_of


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("image/png", ImageMediaType.PNG),
        ("image/jpeg", ImageMediaType.JPEG),
        ("image/webp", ImageMediaType.WEBP),
        ("Image/PNG; charset=binary", ImageMediaType.PNG),
        (" image/webp ", ImageMediaType.WEBP),
    ],
)
def test_a_label_names_a_format_whatever_its_case_or_parameters(
    label: str, expected: ImageMediaType
) -> None:
    assert media_type_of(label) is expected


@pytest.mark.parametrize("label", [None, "", "image/gif", "image/svg+xml", "text/plain", "png"])
def test_a_missing_or_foreign_label_names_no_format(label: str | None) -> None:
    assert media_type_of(label) is None


@pytest.mark.parametrize(
    ("media_type", "body"),
    [
        (ImageMediaType.PNG, png()),
        (ImageMediaType.JPEG, jpeg()),
        (ImageMediaType.WEBP, webp()),
    ],
)
def test_a_body_that_begins_as_its_format_does_looks_like_it(
    media_type: ImageMediaType, body: bytes
) -> None:
    assert looks_like(media_type, body)


@pytest.mark.parametrize(
    ("media_type", "body"),
    [
        (ImageMediaType.PNG, jpeg()),
        (ImageMediaType.JPEG, png()),
        (ImageMediaType.WEBP, png()),
        (ImageMediaType.WEBP, b"RIFF" + bytes(4) + b"WAVE" + bytes(8)),  # A WAV is RIFF too.
        (ImageMediaType.PNG, b""),
        (ImageMediaType.WEBP, b"RIFF"),
    ],
    ids=["png-as-jpeg", "jpeg-as-png", "png-as-webp", "wav-as-webp", "empty", "truncated-webp"],
)
def test_a_body_that_begins_as_another_thing_does_not(
    media_type: ImageMediaType, body: bytes
) -> None:
    assert not looks_like(media_type, body)
