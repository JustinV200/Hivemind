"""Tests for hivemind.swarm.session.paths: a device's paths judged by their text alone.

Fits into the Hive:
    Mirrors src/hivemind/swarm/session/paths.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.session.paths for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cells import make_real_cell_lease

from hivemind.cell.errors import PathNotAllowedError
from hivemind.cell.lease import RealCellLease
from hivemind.swarm.session.paths import DevicePaths

_POSIX_SCRATCH = Path("/pollen/scratch/lease_1")
_WINDOWS_SCRATCH = Path("C:\\Users\\op\\scratch\\lease_1")


def _paths(scratch: Path, *allowed: Path) -> tuple[DevicePaths, RealCellLease]:
    """Build the path rules for a lease whose scratch root is `scratch` on some device."""
    lease = make_real_cell_lease(scratch, allowed_paths=allowed)
    return DevicePaths(lease), lease


def test_a_relative_path_is_joined_under_the_scratch_root() -> None:
    paths, _ = _paths(_POSIX_SCRATCH)

    assert paths.resolve(Path("a/b.txt")) == "/pollen/scratch/lease_1/a/b.txt"


def test_dot_and_dot_dot_segments_collapse() -> None:
    paths, _ = _paths(_POSIX_SCRATCH)

    assert paths.resolve("./a/../b/./c") == "/pollen/scratch/lease_1/b/c"
    assert paths.resolve("../../outside") == "/pollen/outside"


def test_an_absolute_path_is_kept_and_normalised() -> None:
    paths, _ = _paths(_POSIX_SCRATCH)

    assert paths.resolve("/etc//hosts") == "/etc/hosts"


def test_the_scratch_root_and_everything_beneath_it_is_inside() -> None:
    paths, _ = _paths(_POSIX_SCRATCH)

    assert paths.is_inside_scratch("/pollen/scratch/lease_1")
    assert paths.is_inside_scratch("/pollen/scratch/lease_1/deep/file")
    assert not paths.is_inside_scratch("/pollen/scratch/lease_10/file")  # A sibling, not beneath.
    assert not paths.is_inside_scratch("/pollen/scratch")


def test_allowed_paths_extend_the_reach_and_are_read_live() -> None:
    paths, lease = _paths(_POSIX_SCRATCH, Path("/srv/work"))

    assert paths.is_reachable("/srv/work/file")
    assert not paths.is_reachable("/srv/other/file")
    lease.note_allowed_path(Path("/srv/other"))
    assert paths.is_reachable("/srv/other/file")


def test_require_reach_raises_path_not_allowed_for_an_unreachable_path() -> None:
    paths, _ = _paths(_POSIX_SCRATCH)

    paths.require_reach("/pollen/scratch/lease_1/ok")
    with pytest.raises(PathNotAllowedError) as raised:
        paths.require_reach("/etc/shadow")
    assert raised.value.path == Path("/etc/shadow")


def test_require_inside_scratch_refuses_even_an_allowed_path_outside_it() -> None:
    paths, _ = _paths(_POSIX_SCRATCH, Path("/srv/work"))

    paths.require_inside_scratch("/pollen/scratch/lease_1/log")
    with pytest.raises(PathNotAllowedError):
        paths.require_inside_scratch("/srv/work/log")


def test_a_windows_scratch_root_is_read_as_a_windows_path() -> None:
    paths, _ = _paths(_WINDOWS_SCRATCH)

    assert paths.scratch_root == "C:\\Users\\op\\scratch\\lease_1"
    assert paths.resolve("sub\\file.txt") == "C:\\Users\\op\\scratch\\lease_1\\sub\\file.txt"
    assert paths.resolve("C:\\Windows\\..\\Temp") == "C:\\Temp"


def test_windows_paths_compare_case_insensitively_and_across_slashes() -> None:
    paths, _ = _paths(_WINDOWS_SCRATCH)

    assert paths.is_inside_scratch("c:/users/OP/SCRATCH/lease_1/file")
    assert not paths.is_inside_scratch("D:\\Users\\op\\scratch\\lease_1\\file")  # Another drive.


def test_a_unc_scratch_root_is_a_windows_path() -> None:
    paths, _ = _paths(Path("\\\\server\\share\\scratch"))

    assert paths.is_inside_scratch("\\\\server\\share\\scratch\\file")


def test_basename_uses_the_devices_separator() -> None:
    posix, _ = _paths(_POSIX_SCRATCH)
    windows, _ = _paths(_WINDOWS_SCRATCH)

    assert posix.basename("/usr/bin/ls") == "ls"
    assert windows.basename("C:\\Windows\\System32\\cmd.exe") == "cmd.exe"


def test_a_relative_allowed_path_never_matches() -> None:
    paths, _ = _paths(_POSIX_SCRATCH, Path("relative/dir"))

    assert not paths.is_reachable("/relative/dir/file")
