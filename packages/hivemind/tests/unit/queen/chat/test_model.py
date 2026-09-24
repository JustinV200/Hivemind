"""Tests for hivemind.queen.chat.model: ChatEntry round-trips and refuses a mismatched line.

Fits into the Hive:
    Mirrors src/hivemind/queen/chat/model.py (codingrules section 3; 14.3: every pydantic
    boundary model has a round-trip test and a rejection test).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.chat.model for ChatEntry.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.queen.chat import (
    MAX_CHAT_TEXT_CHARS,
    ChatAuthor,
    ChatEntry,
    ChatKind,
    new_chat_entry_id,
)
from waggle.clock import FakeClock
from waggle.ids import new_device_id, new_task_id


def _human(clock: FakeClock, **overrides: object) -> ChatEntry:
    fields: dict[str, object] = {
        "id": new_chat_entry_id(clock),
        "at": clock.now(),
        "author": ChatAuthor.HUMAN,
        "kind": ChatKind.MESSAGE,
        "text": "Is the haiku done?",
        "device_id": new_device_id(clock),
    }
    fields.update(overrides)
    return ChatEntry.model_validate(fields)


def test_a_line_round_trips_through_json() -> None:
    clock = FakeClock()
    line = _human(clock, task_id=new_task_id(clock), seq=4, handled_at=clock.now())

    assert ChatEntry.model_validate_json(line.model_dump_json()) == line


def test_every_line_is_royal() -> None:
    assert _human(FakeClock()).clearance is HoneyClearance.C2


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": ChatKind.REPLY},  # A human never replies; the Queen does.
        {"device_id": None},  # A human line always names its device.
        {"author": ChatAuthor.QUEEN},  # A Queen line is never a message, nor from a device.
        {"author": ChatAuthor.QUEEN, "kind": ChatKind.REPLY},  # ...and names no device.
        {"clearance": HoneyClearance.C1},  # Nothing launders the chat lower.
        {"text": ""},
        {"text": "x" * (MAX_CHAT_TEXT_CHARS + 1)},
        {"seq": 0},
        {"id": "msg_01J0000000000000000000000A"},
    ],
    ids=str,
)
def test_a_mismatched_line_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _human(FakeClock(), **overrides)


def test_a_queen_line_can_never_be_handled() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        ChatEntry(
            id=new_chat_entry_id(clock),
            at=clock.now(),
            author=ChatAuthor.QUEEN,
            kind=ChatKind.NOTICE,
            text="Heads up.",
            handled_at=clock.now(),
        )
