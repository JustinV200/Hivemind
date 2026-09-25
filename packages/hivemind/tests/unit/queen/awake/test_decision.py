"""Tests for hivemind.queen.awake.decision.QueenDecision: a REPLY's words, and only a REPLY's.

Fits into the Hive:
    Mirrors src/hivemind/queen/awake/decision.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.awake.decision for the model under test.
"""

from __future__ import annotations

import pydantic
import pytest

from hivemind.queen.autopilot import QueenAction
from hivemind.queen.awake import QueenDecision
from hivemind.queen.awake.decision import MAX_MESSAGE_CHARS


def test_a_reply_carries_its_words() -> None:
    decision = QueenDecision(action=QueenAction.REPLY, reason="Asked.", message="It is done.")

    assert decision.message == "It is done."


@pytest.mark.parametrize("message", [None, "", "   \n"])
def test_a_reply_without_words_is_refused(message: str | None) -> None:
    # A REPLY with nothing in it would answer the human with silence.
    with pytest.raises(pydantic.ValidationError, match="REPLY decision must carry"):
        QueenDecision(action=QueenAction.REPLY, reason="Asked.", message=message)


def test_words_on_any_other_action_are_refused() -> None:
    # Words nobody would ever read: the ladder's own retry is where a model corrects it.
    with pytest.raises(pydantic.ValidationError, match="only for a REPLY"):
        QueenDecision(action=QueenAction.RECORD, reason="Noted.", message="Noted it.")


def test_a_reply_longer_than_its_bound_is_refused() -> None:
    with pytest.raises(pydantic.ValidationError):
        QueenDecision(
            action=QueenAction.REPLY, reason="Asked.", message="x" * (MAX_MESSAGE_CHARS + 1)
        )
