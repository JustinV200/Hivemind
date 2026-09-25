"""Test hivemind.cli.landing.text: foreign text never reaches a terminal as control, nor a value.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/text.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cli.landing import describe, shown
from hivemind.entrance.models import ApprovalBody


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Pixel 9", "Pixel 9"),
        ("evil\x1b[2J", "evil\\x1b[2J"),  # An escape sequence that would clear the screen.
        ("abc‮dcba", "abc\\u202edcba"),  # A right-to-left override that reverses what follows.
        ("two\nlines", "two\\x0alines"),
        ("\U000e0041tag", "\\U000e0041tag"),  # An invisible tag character.
        ("", "-"),
        (None, "-"),
    ],
)
def test_shown_escapes_everything_a_terminal_would_act_on(text: str | None, expected: str) -> None:
    assert shown(text) == expected


def test_shown_cuts_a_long_string_and_says_so() -> None:
    cut = shown("x" * 500, limit=20)

    assert len(cut) == 20 and cut.endswith("...")


def test_a_refused_value_is_named_by_its_field_never_shown() -> None:
    with pytest.raises(ValidationError) as refused:
        ApprovalBody.model_validate(
            {"name": "laptop", "spend_cap_usd_per_day": -1, "capabilities": ["secret\nvalue"]}
        )

    said = describe(refused.value)

    assert "spend_cap_usd_per_day" in said and "capabilities.0" in said
    assert "secret" not in said and "-1" not in said


def test_any_other_failure_is_its_own_sentence_or_its_name() -> None:
    assert describe(OSError("disk full")) == "disk full"
    assert describe(TimeoutError()) == "TimeoutError"
