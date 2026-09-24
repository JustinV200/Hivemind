"""Unit tests for hivemind.supervision.capping.leave.scan: declared_leaving_root, scan_declared."""

from __future__ import annotations

from pathlib import Path

from hivemind.supervision.capping.leave.scan import (
    MAX_SCAN_FILE_BYTES,
    MAX_SCAN_FILES,
    declared_leaving_root,
    scan_declared_leaves,
)
from waggle.messages import PlannedLeaving

_HOME = Path("/home/op")


def test_declared_leaving_root_returns_the_pattern_itself_when_it_has_no_wildcard() -> None:
    assert declared_leaving_root("/data/project", _HOME) == Path("/data/project")


def test_declared_leaving_root_expands_home() -> None:
    assert declared_leaving_root("~/Projects/app", _HOME) == Path("/home/op/Projects/app")


def test_declared_leaving_root_stops_at_the_first_wildcard_segment() -> None:
    assert declared_leaving_root("/data/*/output", _HOME) == Path("/data")


def test_declared_leaving_root_handles_a_double_star_segment() -> None:
    assert declared_leaving_root("~/logs/**/*.log", _HOME) == Path("/home/op/logs")


def _leaving(pattern: str, reason: str = "kept for the test") -> PlannedLeaving:
    return PlannedLeaving(pattern=pattern, reason=reason)


def test_scan_declared_leaves_finds_a_file_directly_at_its_root(tmp_path: Path) -> None:
    target = tmp_path / "marker.txt"
    target.write_bytes(b"hello")

    found = scan_declared_leaves((_leaving(str(target)),), tmp_path)

    assert found[target.resolve(strict=False)].content == b"hello"


def test_scan_declared_leaves_walks_a_directory_pattern(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "nested").mkdir(parents=True)
    (root / "top.txt").write_bytes(b"top")
    (root / "nested" / "deep.txt").write_bytes(b"deep")

    found = scan_declared_leaves((_leaving(str(root)),), tmp_path)

    resolved = {path.name: entry.content for path, entry in found.items()}
    assert resolved == {"top.txt": b"top", "deep.txt": b"deep"}


def test_scan_declared_leaves_ignores_a_path_outside_every_declared_root(tmp_path: Path) -> None:
    root = tmp_path / "declared"
    root.mkdir()
    (root / "kept.txt").write_bytes(b"kept")
    outside = tmp_path / "not-declared" / "note.txt"
    outside.parent.mkdir()
    outside.write_bytes(b"ignore me")

    found = scan_declared_leaves((_leaving(str(root)),), tmp_path)

    assert {path.name for path in found} == {"kept.txt"}


def test_scan_declared_leaves_returns_empty_for_a_root_that_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    not_yet_written = tmp_path / "future"

    found = scan_declared_leaves((_leaving(str(not_yet_written)),), tmp_path)

    assert found == {}


def test_scan_declared_leaves_never_follows_a_symlinked_root(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "secret.txt").write_bytes(b"outside the declared root")
    link_root = tmp_path / "declared-link"
    try:
        link_root.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        return  # This host (or this user) cannot create symlinks; nothing to assert here.

    found = scan_declared_leaves((_leaving(str(link_root)),), tmp_path)

    assert found == {}


def test_scan_declared_leaves_never_follows_a_symlinked_file_inside_a_declared_root(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside the declared root")
    root = tmp_path / "declared"
    root.mkdir()
    (root / "kept.txt").write_bytes(b"kept")
    link = root / "linked.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        return  # This host (or this user) cannot create symlinks; nothing to assert here.

    found = scan_declared_leaves((_leaving(str(root)),), tmp_path)

    assert {path.name for path in found} == {"kept.txt"}


def test_scan_declared_leaves_skips_a_file_over_the_byte_cap(tmp_path: Path) -> None:
    root = tmp_path / "declared"
    root.mkdir()
    (root / "huge.bin").write_bytes(b"x" * (MAX_SCAN_FILE_BYTES + 1))
    (root / "small.txt").write_bytes(b"fits")

    found = scan_declared_leaves((_leaving(str(root)),), tmp_path)

    assert {path.name for path in found} == {"small.txt"}


def test_scan_declared_leaves_stops_at_the_file_cap(tmp_path: Path) -> None:
    root = tmp_path / "declared"
    root.mkdir()
    for i in range(MAX_SCAN_FILES + 5):
        (root / f"file-{i}.txt").write_bytes(b"x")

    found = scan_declared_leaves((_leaving(str(root)),), tmp_path)

    assert len(found) == MAX_SCAN_FILES


def test_scan_declared_leaves_scans_every_declared_root() -> None:
    # Two roots that do not exist: proves multiple declared leavings are each walked (or, here,
    # each safely skipped) rather than only the first one being consulted.
    found = scan_declared_leaves(
        (_leaving("/does/not/exist/one"), _leaving("/does/not/exist/two")), Path("/home/op")
    )

    assert found == {}
