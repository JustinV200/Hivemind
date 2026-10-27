"""Tests for hivemind.swarm.enrolment.capabilities: what a device is told it may do at each level.

Fits into the Hive:
    Mirrors src/hivemind/swarm/enrolment/capabilities.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one). The strings are the Guard's own ceilings cut to the five families a
    packet can enforce, so these tests hold the two to each other as well as to the table.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.enrolment.capabilities for the module under test.
    - docs/waggle/enrolment.md section 7.1 for the table.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from hivemind.cell import AccessLevel
from hivemind.guard import SCRATCH_PLACEHOLDER, Capability, CapabilityFamily, ceiling_for
from hivemind.swarm.enrolment import (
    PACKET_FAMILIES,
    granted_capabilities,
    grants_read,
    readable_under,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id

_CELL = new_cell_id(FakeClock())
_WATCH = f"watch:{_CELL}"

_EXPECTED = {
    AccessLevel.READ_ONLY: ("fs:read:**", _WATCH),
    # No exec: a borrowed machine does not contain its processes, so commands need FULL (ADR-0060).
    AccessLevel.SCRATCH: ("fs:read:**", "fs:write:{scratch}/**", _WATCH),
    AccessLevel.FULL: (
        "cell:outside_scratch:**",
        "exec:*",
        "fs:read:**",
        "fs:write:**",
        "fs:write:{scratch}/**",
        _WATCH,
    ),
}


@pytest.mark.parametrize("level", list(AccessLevel))
def test_each_level_is_granted_exactly_the_strings_of_the_table(level: AccessLevel) -> None:
    assert granted_capabilities(level, _CELL) == _EXPECTED[level]


@pytest.mark.parametrize("level", list(AccessLevel))
def test_every_string_parses_and_belongs_to_a_family_a_packet_can_enforce(
    level: AccessLevel,
) -> None:
    families = {Capability.parse(text).family for text in granted_capabilities(level, _CELL)}

    assert families <= PACKET_FAMILIES | {CapabilityFamily.WATCH}


@pytest.mark.parametrize("level", list(AccessLevel))
def test_nothing_is_granted_that_the_guard_does_not_allow_the_level(level: AccessLevel) -> None:
    ceiling = {str(c) for c in ceiling_for(level, PurePosixPath(SCRATCH_PLACEHOLDER))}
    granted = set(granted_capabilities(level, _CELL))

    assert granted - {_WATCH} <= ceiling


def test_each_level_grants_everything_the_level_below_it_does() -> None:
    read_only, scratch, full = (granted_capabilities(level, _CELL) for level in AccessLevel)

    assert set(read_only) < set(scratch) < set(full)


def test_a_read_only_device_runs_no_command_and_writes_no_file() -> None:
    granted = granted_capabilities(AccessLevel.READ_ONLY, _CELL)

    assert not [text for text in granted if text.startswith(("exec:", "fs:write:", "cell:"))]


def test_only_the_watch_string_depends_on_the_cell() -> None:
    other = new_cell_id(FakeClock())

    first = set(granted_capabilities(AccessLevel.FULL, _CELL))
    second = set(granted_capabilities(AccessLevel.FULL, other))

    assert first ^ second == {_WATCH, f"watch:{other}"}


def test_the_strings_are_sorted_so_two_calls_agree_byte_for_byte() -> None:
    granted = granted_capabilities(AccessLevel.FULL, _CELL)

    assert list(granted) == sorted(granted)
    assert granted == granted_capabilities(AccessLevel.FULL, _CELL)


def test_the_scratch_placeholder_is_left_for_the_packet_to_fill_per_lease() -> None:
    granted = granted_capabilities(AccessLevel.SCRATCH, _CELL)

    assert f"fs:write:{SCRATCH_PLACEHOLDER}/**" in granted


@pytest.mark.parametrize("level", list(AccessLevel))
@pytest.mark.parametrize("root", ["/var/log", "/srv/app/logs", "C:\\logs", "/"])
def test_every_level_lets_the_hive_read_anywhere_so_any_root_is_readable(
    level: AccessLevel, root: str
) -> None:
    assert grants_read(granted_capabilities(level, _CELL), root)


def test_a_root_outside_every_read_string_is_not_readable() -> None:
    narrow = ("fs:read:/srv/allowed/**", _WATCH)

    assert grants_read(narrow, "/srv/allowed/logs")
    assert not grants_read(narrow, "/srv/other")
    assert not grants_read(narrow, "/srv/allowed")  # The directory itself is not under its own /**.


def test_a_write_string_does_not_let_the_hive_read() -> None:
    assert not grants_read(("fs:write:**", "exec:*", _WATCH), "/var/log")


def test_a_root_the_guards_grammar_refuses_is_not_readable() -> None:
    assert not grants_read(("fs:read:**",), "")


@pytest.mark.parametrize("level", list(AccessLevel))
def test_every_level_lets_a_list_of_directories_through_whole_and_in_order(
    level: AccessLevel,
) -> None:
    roots = ("/var/log/app", "/srv/data", "C:\\Logs")

    assert readable_under(granted_capabilities(level, _CELL), roots) == roots


def test_a_list_is_cut_to_the_directories_the_strings_cover_without_reordering_them() -> None:
    narrow = ("fs:read:/srv/**", _WATCH)

    kept = readable_under(narrow, ("/srv/b", "/var/log", "/srv/a/logs", "/etc"))

    assert kept == ("/srv/b", "/srv/a/logs")


def test_no_directory_is_readable_under_no_read_string() -> None:
    assert readable_under(("exec:*", _WATCH), ("/var/log",)) == ()
    assert readable_under(("fs:read:**",), ()) == ()
