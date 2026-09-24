"""Tests for hivemind.entrance.voice.services: the rules read from [entrance.voice].

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/services.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

from hivemind.entrance.voice import VoiceRules
from hivemind.manifest.schema.entrance import EntranceVoiceSection


def test_the_rules_are_the_voice_sections_own() -> None:
    section = EntranceVoiceSection(
        confirm_goals=False, keep_audio=True, max_clip_seconds=30.0, keep_audio_hours=6.0
    )

    rules = VoiceRules.from_section(section)

    assert rules == VoiceRules(
        confirm_goals=False,
        keep_audio=True,
        max_clip_seconds=30.0,
        retention=timedelta(hours=6),
    )


def test_the_default_rules_confirm_goals_and_keep_nothing() -> None:
    rules = VoiceRules.from_section(EntranceVoiceSection())

    assert (rules.confirm_goals, rules.keep_audio, rules.max_clip_seconds) == (True, False, 120.0)
