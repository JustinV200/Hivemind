"""Tests for hivemind.swarm.operator.roots: what a list of directories to watch may be.

Fits into the Hive:
    Mirrors src/hivemind/swarm/operator/roots.py (codingrules section 3). The rule is pure, so the
    tests build nodes with the capability strings they want and read the answer directly: every
    directory absolute in the device's own spelling, bounded as the wire is, readable under the
    node's granted capabilities, in normal form, each once and none inside another.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.operator.roots for the module under test.
    - hivemind.swarm.operator.desk for the act that calls it.
"""

from __future__ import annotations

import pytest
from builders.swarm import make_device_party, make_swarm_node

from hivemind.swarm.models import SwarmNode
from hivemind.swarm.operator import WatchRootRefusedError, checked_roots
from waggle.clock import FakeClock
from waggle.messages.swarm.watch import MAX_OBSERVE_ROOTS, MAX_OBSERVED_PATH_CHARS


def _node(**overrides: object) -> SwarmNode:
    """A device at the default access level, with `overrides` applied."""
    clock = FakeClock()
    return make_swarm_node(make_device_party(clock), clock, **overrides)


def test_a_list_of_absolute_directories_comes_back_in_the_order_it_was_named() -> None:
    assert checked_roots(_node(), ["/var/log/app", "/srv/data"]) == ("/var/log/app", "/srv/data")


def test_nothing_named_is_nothing_watched() -> None:
    assert checked_roots(_node(), []) == ()


def test_each_directory_is_put_in_its_normal_form_in_the_devices_own_spelling() -> None:
    named = ["/var//log/./app/", "/srv/data/../logs", "C:/Users/op/logs/"]

    assert checked_roots(_node(), named) == ("/var/log/app", "/srv/logs", "C:\\Users\\op\\logs")


@pytest.mark.parametrize("text", ["logs", "./logs", "C:logs", "", "/var/\0log"])
def test_a_directory_that_is_not_absolute_on_the_device_is_refused_by_its_position(
    text: str,
) -> None:
    with pytest.raises(WatchRootRefusedError) as refused:
        checked_roots(_node(), ["/fine", text])

    assert "directory 2 is not an absolute path" in str(refused.value)


def test_a_refusal_names_the_position_of_a_directory_and_never_the_directory() -> None:
    with pytest.raises(WatchRootRefusedError) as refused:
        checked_roots(_node(), ["not-absolute-hush-hush"])

    assert "hush-hush" not in str(refused.value)  # A path is the operator's own data.


def test_a_directory_named_twice_is_kept_once() -> None:
    assert checked_roots(_node(), ["/srv/a", "/srv/b", "/srv/a/"]) == ("/srv/a", "/srv/b")


def test_a_directory_inside_another_one_named_is_dropped_whichever_came_first() -> None:
    # The packet reads a directory's whole tree: the inner one would be reported twice.
    assert checked_roots(_node(), ["/srv/a/deep", "/srv/a", "/srv/b"]) == ("/srv/a", "/srv/b")
    assert checked_roots(_node(), ["/srv/a", "/srv/a/deep"]) == ("/srv/a",)


def test_a_sibling_whose_name_only_starts_the_same_is_not_inside() -> None:
    assert checked_roots(_node(), ["/srv/a", "/srv/ab"]) == ("/srv/a", "/srv/ab")


def test_windows_directories_that_differ_only_in_case_are_the_same_directory() -> None:
    assert checked_roots(_node(), ["C:\\Logs", "c:\\logs"]) == ("C:\\Logs",)


def test_more_directories_than_the_wire_carries_are_refused() -> None:
    named = [f"/srv/{index}" for index in range(MAX_OBSERVE_ROOTS + 1)]

    with pytest.raises(WatchRootRefusedError, match="at most"):
        checked_roots(_node(), named)


def test_the_most_the_wire_carries_are_accepted() -> None:
    named = [f"/srv/{index}" for index in range(MAX_OBSERVE_ROOTS)]

    assert len(checked_roots(_node(), named)) == MAX_OBSERVE_ROOTS


def test_a_directory_longer_than_the_wire_allows_is_refused() -> None:
    with pytest.raises(WatchRootRefusedError, match="longer than"):
        checked_roots(_node(), ["/" + "d" * MAX_OBSERVED_PATH_CHARS])


def test_a_directory_the_devices_capabilities_do_not_let_the_hive_read_is_refused() -> None:
    node = _node(granted_capabilities=("fs:read:/srv/ok/**", "watch:cell_x"))

    assert checked_roots(node, ["/srv/ok/logs"]) == ("/srv/ok/logs",)
    with pytest.raises(WatchRootRefusedError, match="access level"):
        checked_roots(node, ["/srv/ok/logs", "/srv/elsewhere"])


def test_the_refusal_carries_a_stable_code_and_the_device() -> None:
    node = _node()

    with pytest.raises(WatchRootRefusedError) as refused:
        checked_roots(node, ["relative"])

    assert refused.value.code == "hive.swarm.watch_root_refused"
    assert refused.value.device_id == node.device_id
