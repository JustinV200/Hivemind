"""Tests for hivemind.wardens.watch.probe.commands: the fixed commands a probe may run.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/probe/commands.py (codingrules section 3). The scripts are
    audited as text: what they may contain is a closed list, and nothing from outside is ever
    formatted into them.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.probe.commands for the module under test.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest

from hivemind.wardens.watch.probe.commands import (
    LISTING_BYTES,
    PROCESS_BYTES,
    SYSTEM_TIMEOUT_S,
    TAIL_BYTES,
    TAIL_TIMEOUT_S,
    sections_for,
    system_argv,
    tail_argv,
)
from waggle.messages.swarm import ObservationKind

_MARK = "@@0123456789abcdef"
_WRITERS = (
    "rm ",
    "mv ",
    "cp ",
    "tee ",
    "dd ",
    "mkdir",
    "touch",
    "chmod",
    "chown",
    "kill",
    "curl",
    "wget",
    "truncate",
    "sed -i",
    "-delete",
    "-exec",
    "-execdir",
    "-ok",
    "-fprint",
    "-fls",
    "sudo",
    "eval",
)


def _all_system_script() -> str:
    """The script that reads everything: the widest thing the probe ever runs."""
    sections = sections_for(list(ObservationKind))
    return system_argv(sections, _MARK, "/scratch", ["/var/log"])[2]


def test_sections_for_returns_each_needed_section_once_in_a_fixed_order() -> None:
    both = sections_for([ObservationKind.RESOURCE_USE, ObservationKind.PROCESS_LIST])
    swapped = sections_for([ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE])

    assert both == swapped  # The order asked for does not matter.
    assert len(set(both)) == len(both)
    assert both.count("stat") == 1  # Shared by the process list and resource use.


def test_logs_and_file_changes_share_one_listing() -> None:
    assert sections_for([ObservationKind.LOGS]) == ("roots",)
    assert sections_for([ObservationKind.FILE_CHANGES]) == ("roots",)
    assert sections_for([ObservationKind.LOGS, ObservationKind.FILE_CHANGES]) == ("roots",)


def test_a_kind_that_reads_no_file_never_lists_one() -> None:
    sections = sections_for([ObservationKind.PROCESS_LIST, ObservationKind.RESOURCE_USE])
    script = system_argv(sections, _MARK, "/scratch", [])[2]

    assert "find" not in script
    assert "roots" not in sections


def test_the_script_is_the_same_for_the_same_sections_whatever_is_passed_in() -> None:
    sections = sections_for(list(ObservationKind))

    first = system_argv(sections, "@@aaaa", "/one", ["/x"])
    second = system_argv(sections, "@@bbbb", "/two", ["/y", "/z"])

    assert first[2] == second[2]
    assert first[:4] == second[:4] == ("sh", "-c", first[2], "sh")


def test_the_mark_the_directory_and_the_roots_are_parameters_not_script_text() -> None:
    hostile = "/a b/$(touch pwned)/`id`;rm -rf /"

    argv = system_argv(sections_for(list(ObservationKind)), _MARK, "/scr atch", [hostile, "/ok"])

    assert argv[4:] == (_MARK, "/scr atch", hostile, "/ok")
    assert hostile not in argv[2]
    assert "/scr atch" not in argv[2]
    assert _MARK not in argv[2]


def test_tail_argv_puts_the_mark_then_every_path_after_the_script() -> None:
    paths = ["/var/log/a.log", "/var/log/-rf.log"]

    argv = tail_argv(_MARK, paths)

    assert argv[:2] == ("sh", "-c")
    assert argv[3:] == ("sh", _MARK, *paths)
    assert all(path not in argv[2] for path in paths)


@pytest.mark.parametrize("script_of", [_all_system_script, lambda: tail_argv(_MARK, [])[2]])
def test_no_script_contains_a_write_a_delete_a_signal_or_a_network_tool(
    script_of: Callable[[], str],
) -> None:
    script = script_of()

    for word in _WRITERS:
        assert word not in script, word


@pytest.mark.parametrize("script_of", [_all_system_script, lambda: tail_argv(_MARK, [])[2]])
def test_the_only_redirection_in_any_script_discards_standard_error(
    script_of: Callable[[], str],
) -> None:
    without_discard = script_of().replace("2>/dev/null", "")

    assert ">" not in without_discard  # No file is ever opened for writing.
    assert "<" not in without_discard  # Nor is one fed to a command's input.


def test_find_lists_regular_files_under_each_root_and_follows_nothing_below_it() -> None:
    script = _all_system_script()

    assert "find -H" in script  # Only the root itself, if it is a link.
    assert "-type f" in script  # No directory, link or device is listed.
    assert re.search(r"-printf '[^']*\\0'", script)  # NUL-terminated records.
    assert "-maxdepth" in script


def test_every_volume_a_command_can_produce_is_bounded_by_head() -> None:
    script = _all_system_script()

    assert f"head -c {PROCESS_BYTES}" in script
    assert f"head -c {LISTING_BYTES}" in script


def test_the_tail_script_reads_only_a_plain_file_and_only_its_end() -> None:
    script = tail_argv(_MARK, ["/x"])[2]

    assert f"tail -c {TAIL_BYTES}" in script
    assert '[ -f "$file" ]' in script
    assert '[ ! -L "$file" ]' in script  # Not a link that appeared since the listing.


def test_the_timeouts_are_named_and_the_second_command_gets_less_than_the_first() -> None:
    assert 0 < TAIL_TIMEOUT_S < SYSTEM_TIMEOUT_S
