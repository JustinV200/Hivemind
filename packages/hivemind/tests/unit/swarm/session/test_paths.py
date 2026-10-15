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

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
from builders.cells import make_identity, make_lease_request

from hivemind.cell.errors import PathNotAllowedError
from hivemind.cell.fake import FakeLeaseReleaser
from hivemind.cell.lease import LeaseFacts, RealCellLease
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.swarm.session.paths import (
    DevicePathRule,
    DevicePaths,
    absolute_device_path,
    device_text,
)
from waggle.clock import FakeClock
from waggle.ids import new_lease_id

# Scratch roots as a device's `cell.lease_opened` spells them: text, which no host rewrites.
_POSIX_SCRATCH = "/pollen/scratch/lease_1"
_WINDOWS_SCRATCH = "C:\\Users\\op\\scratch\\lease_1"


def _paths(scratch: str, *allowed: Path) -> tuple[DevicePaths, RealCellLease]:
    """Build the path rules for a lease whose scratch root is `scratch` on some device."""
    lease = _device_lease(scratch, allowed)
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
    paths, _ = _paths("\\\\server\\share\\scratch")

    assert paths.is_inside_scratch("\\\\server\\share\\scratch\\file")


def test_basename_uses_the_devices_separator() -> None:
    posix, _ = _paths(_POSIX_SCRATCH)
    windows, _ = _paths(_WINDOWS_SCRATCH)

    assert posix.basename("/usr/bin/ls") == "ls"
    assert windows.basename("C:\\Windows\\System32\\cmd.exe") == "cmd.exe"


def test_a_relative_allowed_path_never_matches() -> None:
    paths, _ = _paths(_POSIX_SCRATCH, Path("relative/dir"))

    assert not paths.is_reachable("/relative/dir/file")


def _device_lease(scratch: str, allowed: tuple[Path, ...] = ()) -> RealCellLease:
    """A lease on a device: scratch root `scratch`, paths judged by the device's own rule."""
    # As `swarm.source` builds one: the device's text held as a `Path`, judged by the device's
    # rule, so widening it never asks the Hive Stand's disk (which puts `/srv` on a drive).
    clock = FakeClock()
    request = make_lease_request(clock=clock)
    facts = LeaseFacts(
        id=new_lease_id(clock),
        cell_id=request.cell_id,
        holder=request.holder,
        task_id=None,
        scratch_root=Path(scratch),
        access_level=AccessLevel.SCRATCH,
        comb_shield=CombShieldLevel.MEADOW,
        allowed_paths=allowed,
        paths=DevicePathRule(scratch),
    )
    return RealCellLease(
        facts,
        trail=MemoryPheromoneTrail(clock),
        clock=clock,
        identity=make_identity(clock=clock),
        releaser=FakeLeaseReleaser(),
    )


def test_the_rule_reads_a_relative_path_under_the_devices_scratch_without_a_disk() -> None:
    rule = DevicePathRule(_POSIX_SCRATCH)

    assert rule.resolve(Path("a/../b.txt")) == Path("/pollen/scratch/lease_1/b.txt")
    assert rule.resolve(Path("/etc//hosts")) == Path("/etc/hosts")


def test_the_rule_resolves_a_windows_path_in_the_devices_own_spelling() -> None:
    rule = DevicePathRule(_WINDOWS_SCRATCH)

    resolved = rule.resolve(Path("C:\\Users\\op\\scratch\\lease_1\\a\\..\\b.txt"))

    assert str(resolved) == "C:\\Users\\op\\scratch\\lease_1\\b.txt"
    assert str(rule.resolve(Path("notes.txt"))) == "C:\\Users\\op\\scratch\\lease_1\\notes.txt"


def test_the_rule_says_within_by_the_devices_case_rules() -> None:
    posix, windows = DevicePathRule(_POSIX_SCRATCH), DevicePathRule(_WINDOWS_SCRATCH)

    assert posix.is_within(Path("/srv/work/a"), Path("/srv/work"))
    assert not posix.is_within(Path("/srv/work2/a"), Path("/srv/work"))  # A shared prefix only.
    assert not posix.is_within(Path("/SRV/work/a"), Path("/srv/work"))  # POSIX is case-sensitive.
    assert windows.is_within(Path("c:\\USERS\\op\\a"), Path("C:\\Users\\op"))
    assert not windows.is_within(Path("D:\\Users\\op\\a"), Path("C:\\Users\\op"))


def test_the_session_and_the_lease_agree_on_what_a_path_is() -> None:
    lease = _device_lease(_WINDOWS_SCRATCH)
    paths = DevicePaths(lease)

    for text in ("sub\\file.txt", "C:\\Windows\\..\\Temp", "a\\.\\b"):
        assert paths.resolve(text) == str(DevicePathRule(_WINDOWS_SCRATCH).resolve(Path(text)))


def test_a_device_lease_judges_a_path_by_its_text_whatever_the_hive_stands_disk_holds(
    tmp_path: Path,
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (tmp_path / "outside").mkdir()
    (scratch / "link").symlink_to(tmp_path / "outside")  # A link on the Hive Stand's own disk.
    lease = _device_lease(str(scratch))  # A device that spells its scratch as this host does.

    # On the device the link is the packet's to judge: the Hive's wall is lexical.
    assert lease.is_path_allowed(scratch / "link" / "file")
    assert lease.is_path_allowed(Path("notes.txt"))  # Relative: under the device's scratch.
    assert not lease.is_path_allowed(Path("../outside/file"))
    assert not lease.is_path_allowed(tmp_path / "outside" / "file")


def test_a_windows_device_lease_works_whatever_this_hive_stand_is() -> None:
    lease = _device_lease(_WINDOWS_SCRATCH)

    assert lease.is_path_allowed(Path("C:\\Users\\op\\scratch\\lease_1\\x\\..\\y.txt"))
    assert lease.is_path_allowed(Path("y.txt"))
    assert not lease.is_path_allowed(Path("C:\\Users\\op\\other\\y.txt"))
    assert not lease.is_path_allowed(Path("C:\\Users\\op\\scratch\\lease_1\\..\\lease_2\\y.txt"))


def test_widening_a_device_lease_stores_the_devices_normal_form() -> None:
    lease = _device_lease(_WINDOWS_SCRATCH)

    lease.note_allowed_path(Path("C:\\Users\\op\\project\\..\\project"))

    assert [str(p) for p in lease.allowed_paths] == ["C:\\Users\\op\\project"]
    assert lease.is_path_allowed(Path("c:\\users\\OP\\project\\src\\main.py"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/var/log/app", "/var/log/app"),
        ("/var/log/app/", "/var/log/app"),
        ("/var//log/./app/../app", "/var/log/app"),
        ("/", "/"),
        ("C:\\logs", "C:\\logs"),
        ("C:/logs/", "C:\\logs"),
        ("c:\\Users\\op\\.\\logs", "c:\\Users\\op\\logs"),
        ("\\\\server\\share\\logs", "\\\\server\\share\\logs"),
    ],
)
def test_a_device_path_that_is_absolute_in_the_devices_spelling_is_normalised(
    text: str, expected: str
) -> None:
    # The answer is the same whatever system the test runs on: the device's spelling decides.
    assert absolute_device_path(text) == expected


@pytest.mark.parametrize(
    "text", ["", "logs", "./logs", "../logs", "C:logs", "C:", "var/log", "/var/\0log", "\0"]
)
def test_a_relative_empty_or_nul_holding_path_is_not_an_absolute_device_path(text: str) -> None:
    assert absolute_device_path(text) is None


@pytest.mark.parametrize(
    ("held", "expected"),
    [
        # What `Path` makes of a POSIX device's text on a Windows Hive Stand: its own separators.
        (PureWindowsPath("/pollen/scratch/lease_1"), "/pollen/scratch/lease_1"),
        (PureWindowsPath("a/../b.txt"), "a/../b.txt"),
        # A drive or a UNC share is a Windows device's path, and keeps the Windows spelling.
        (PureWindowsPath("C:/Users/op"), "C:\\Users\\op"),
        (PureWindowsPath("\\\\server\\share\\x"), "\\\\server\\share\\x"),
        # A POSIX Hive Stand's `Path` never rewrites a separator, so a device's text survives it.
        (PurePosixPath("C:\\Users\\op"), "C:\\Users\\op"),
        (PurePosixPath("/pollen/scratch"), "/pollen/scratch"),
        ("/already/text", "/already/text"),
    ],
)
def test_a_device_path_held_as_a_path_reads_back_in_the_devices_spelling(
    held: PurePosixPath | PureWindowsPath | str, expected: str
) -> None:
    # Pure paths of both flavours, so this holds on every system the suite runs on.
    assert device_text(held) == expected
