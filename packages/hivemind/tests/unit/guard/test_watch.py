"""Tests for hivemind.guard.watch: watch mode sees what READ_ONLY allows, and never a person.

Fits into the Hive:
    Mirrors src/hivemind/guard/watch.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.watch for the table under test.
"""

from __future__ import annotations

import pytest

from hivemind.cell.tiers import AccessLevel
from hivemind.guard.watch import WATCH_OBSERVATIONS, WatchObservation, watch_permits

_ALLOWED = {
    WatchObservation.PROCESS_LIST,
    WatchObservation.RESOURCE_USE,
    WatchObservation.LOGS,
    WatchObservation.FILE_CHANGES,
}


@pytest.mark.parametrize("level", list(AccessLevel))
def test_every_level_watches_exactly_what_read_only_allows(level: AccessLevel) -> None:
    assert WATCH_OBSERVATIONS[level] == _ALLOWED


@pytest.mark.parametrize("level", list(AccessLevel))
@pytest.mark.parametrize(
    "capture", [WatchObservation.SCREEN_CAPTURE, WatchObservation.INPUT_CAPTURE]
)
def test_the_screen_and_the_input_are_never_watch_mode(
    level: AccessLevel, capture: WatchObservation
) -> None:
    assert watch_permits(level, capture) is False


def test_the_table_names_every_level() -> None:
    assert set(WATCH_OBSERVATIONS) == set(AccessLevel)
