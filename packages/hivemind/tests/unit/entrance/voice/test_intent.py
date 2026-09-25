"""Tests for hivemind.entrance.voice.intent: goal, chat and answer:<question id>, and nothing else.

Fits into the Hive:
    Mirrors src/hivemind/entrance/voice/intent.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import re

import pytest

from hivemind.entrance.voice import (
    ANSWER,
    INTENT_PATTERN,
    SUBMIT,
    InvalidIntentError,
    VoiceIntent,
    VoiceIntentKind,
)
from waggle.clock import FakeClock
from waggle.ids import new_message_id

_QUESTION = new_message_id(FakeClock())  # A well-formed question id.


@pytest.mark.parametrize(
    ("raw", "kind", "capability"),
    [("goal", VoiceIntentKind.GOAL, SUBMIT), ("chat", VoiceIntentKind.CHAT, SUBMIT)],
)
def test_a_bare_intent_parses_to_its_kind_and_needs_submit(
    raw: str, kind: VoiceIntentKind, capability: str
) -> None:
    intent = VoiceIntent.parse(raw)

    assert (intent.kind, intent.question_id, intent.capability) == (kind, None, capability)
    assert str(intent) == raw


def test_an_answer_intent_names_its_question_and_needs_answer() -> None:
    raw = f"answer:{_QUESTION}"

    intent = VoiceIntent.parse(raw)

    assert (intent.kind, intent.question_id, intent.capability) == (
        VoiceIntentKind.ANSWER,
        _QUESTION,
        ANSWER,
    )
    assert str(intent) == raw
    assert re.fullmatch(INTENT_PATTERN, raw)


@pytest.mark.parametrize(
    "raw",
    ["", "GOAL", "answer", "answer:", "answer:task_01ARZ3NDEKTSV4RRFFQ69G5FAV", "goal ", "shout"],
)
def test_anything_else_is_refused(raw: str) -> None:
    with pytest.raises(InvalidIntentError):
        VoiceIntent.parse(raw)

    assert re.fullmatch(INTENT_PATTERN, raw) is None


def test_an_answer_without_a_question_or_a_goal_with_one_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="names its question"):
        VoiceIntent(VoiceIntentKind.ANSWER)
    with pytest.raises(ValueError, match="names its question"):
        VoiceIntent(VoiceIntentKind.GOAL, _QUESTION)
