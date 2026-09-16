"""Tests for hivemind.queen.autopilot.forage: decide_forage_request.

Fits into the Hive:
    Mirrors src/hivemind/queen/autopilot/forage.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.autopilot.forage for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.queen.autopilot.forage import (
    ForageAutopilotOutcome,
    ForageRequestSignal,
    decide_forage_request,
)


@pytest.mark.parametrize(
    ("within_headroom", "shrinkable", "expected"),
    [
        (True, False, ForageAutopilotOutcome.GRANT),
        (True, True, ForageAutopilotOutcome.GRANT),
        (False, True, ForageAutopilotOutcome.NEEDS_JUDGEMENT),
        (False, False, ForageAutopilotOutcome.DENY),
    ],
)
def test_decide_forage_request_covers_every_combination(
    within_headroom: bool, shrinkable: bool, expected: ForageAutopilotOutcome
) -> None:
    signal = ForageRequestSignal(within_headroom=within_headroom, shrinkable=shrinkable)

    assert decide_forage_request(signal) is expected


def test_decide_forage_request_is_pure() -> None:
    signal = ForageRequestSignal(within_headroom=False, shrinkable=True)

    first = decide_forage_request(signal)
    second = decide_forage_request(signal)

    assert first is second is ForageAutopilotOutcome.NEEDS_JUDGEMENT
