"""Test path resolution: decide on what a path really is, then on what it needs.

A Real Cell (an existing device the Hive borrows) is left exactly as found (gateway-contract
section 12). For every path in a request the Pollen Packet (the thin gateway program on the device)
removes ``.`` and ``..`` segments and resolves symbolic links *before* deciding whether the path is
inside the lease's scratch directory and which capability it needs, and it acts on the path it
checked, not on the text it was sent. A link inside scratch that points outside is outside.
Writing or deleting needs ``fs:write``, and also ``cell:outside_scratch`` outside scratch; reading
needs ``fs:read``. These tests resolve real paths in a temporary directory.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.paths.

Key invariants:
    - A relative path joins under scratch; an absolute one stands alone.
    - The check is made on the resolved path, never the text.

See Also:
    - docs/waggle/gateway-contract.md sections 10 and 12.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pollen.errors import PathRefusedError
from pollen.executors import Need
from pollen.executors.base import FS_READ, FS_WRITE, OUTSIDE_SCRATCH
from pollen.executors.paths import is_within, read_needs, resolve_path, write_needs


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A resolved scratch directory for a lease."""
    root = (tmp_path / "scratch").resolve()
    root.mkdir()
    return root


def test_a_relative_path_joins_under_scratch_and_dots_are_removed(scratch: Path) -> None:
    assert resolve_path(scratch, "a/./b/../c.txt", "lease_x") == scratch / "a" / "c.txt"
    assert resolve_path(scratch, "new.txt", "lease_x") == scratch / "new.txt"


def test_an_absolute_path_stands_alone(scratch: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path.resolve() / "elsewhere" / "file.txt"
    assert resolve_path(scratch, str(elsewhere), "lease_x") == elsewhere


def test_dotdot_can_climb_out_of_scratch_and_the_result_says_so(scratch: Path) -> None:
    resolved = resolve_path(scratch, "../escape.txt", "lease_x")
    assert resolved == scratch.parent / "escape.txt"
    assert not is_within(scratch, resolved)


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links")
def test_a_link_inside_scratch_that_points_outside_resolves_to_outside(
    scratch: Path, tmp_path: Path
) -> None:
    outside = tmp_path.resolve() / "outside"
    outside.mkdir()
    (scratch / "door").symlink_to(outside, target_is_directory=True)
    resolved = resolve_path(scratch, "door/file.txt", "lease_x")
    assert resolved == outside / "file.txt" and not is_within(scratch, resolved)


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links")
def test_a_link_loop_is_refused(scratch: Path) -> None:
    (scratch / "a").symlink_to(scratch / "b")
    (scratch / "b").symlink_to(scratch / "a")
    with pytest.raises(PathRefusedError, match="cannot be resolved safely"):
        resolve_path(scratch, "a/file", "lease_x")


@pytest.mark.parametrize("text", ["", "bad\x00name"])
def test_an_empty_path_or_one_with_a_nul_byte_is_refused(scratch: Path, text: str) -> None:
    with pytest.raises(PathRefusedError):
        resolve_path(scratch, text, "lease_x")


def test_within_means_the_root_itself_or_something_below_it(scratch: Path) -> None:
    assert is_within(scratch, scratch) and is_within(scratch, scratch / "a" / "b")
    assert not is_within(scratch, scratch.parent)
    assert not is_within(scratch, Path(str(scratch) + "-sibling") / "x")


def test_writing_inside_scratch_needs_only_the_write_grant(scratch: Path) -> None:
    target = scratch / "out.txt"
    assert write_needs(target, scratch) == (Need(FS_WRITE, str(target)),)


def test_writing_outside_scratch_also_needs_the_outside_grant(scratch: Path) -> None:
    target = scratch.parent / "out.txt"
    assert write_needs(target, scratch) == (
        Need(FS_WRITE, str(target)),
        Need(OUTSIDE_SCRATCH, str(target)),
    )


def test_reading_needs_only_the_read_grant_in_or_out_of_scratch(scratch: Path) -> None:
    for target in (scratch / "in.txt", scratch.parent / "out.txt"):
        assert read_needs(target) == (Need(FS_READ, str(target)),)
