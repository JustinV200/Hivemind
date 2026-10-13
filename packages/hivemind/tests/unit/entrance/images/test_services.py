"""Tests for hivemind.entrance.images.services: the rules read from [entrance].

Fits into the Hive:
    Mirrors src/hivemind/entrance/images/services.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

from hivemind.entrance.images import ImageRules
from hivemind.manifest.schema.entrance import EntranceSection


def test_the_rules_are_the_entrance_sections_own() -> None:
    section = EntranceSection(max_image_bytes=1_000_000, image_keep_hours=6.0)

    rules = ImageRules.from_section(section)

    assert rules == ImageRules(max_bytes=1_000_000, retention=timedelta(hours=6))


def test_the_default_rules_take_eight_mebibytes_and_keep_a_day() -> None:
    rules = ImageRules.from_section(EntranceSection())

    assert rules == ImageRules(max_bytes=8 * 1024 * 1024, retention=timedelta(hours=24))
