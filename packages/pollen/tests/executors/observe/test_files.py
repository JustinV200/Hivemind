"""Test the file half of watch mode: the bounded walk, the log tail and the change watcher.

A Warden (the per-Cell supervisor) watching an idle Real Cell (a device the Hive borrows) may ask
its Pollen Packet (the thin gateway program on the device) for the tail of the log files under
some roots and for the files created, changed or deleted there since a cursor (ADR-0054). The
collectors read the file system directly and run nothing. They never follow a symlink, they stop
at a bound in entries and depth so a huge tree cannot stall the device, and a path longer than the
wire allows is skipped, because a cut path would name another file. These tests build real
directory trees, date their files, and read what the collectors report, including every cut.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.observe.files.

Key invariants:
    - Nothing a test watches is changed by watching it.
    - A bound is exercised by shrinking it, not by building a tree as big as the real one.

See Also:
    - docs/waggle/gateway-contract.md section 9.4 and ADR-0054.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pollen.executors.observe import files as observe_files
from pollen.executors.observe.files import ChangeWatcher, scan_root, tail_logs
from waggle.messages.swarm import FileChangeKind
from waggle.messages.swarm.watch import MAX_LOG_LINE_CHARS, MAX_LOG_LINES


def _write(path: Path, text: str = "x", age_s: float = 0.0) -> Path:
    """Write ``text`` to ``path`` (making its folders) and date it ``age_s`` seconds ago."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
    return path


def _too_long_dir(root: Path) -> Path:
    """A directory whose path, with a file name added, passes the wire's path bound."""
    return root / ("d" * 120) / ("e" * 120)


def test_the_walk_lists_regular_files_with_their_time_and_size(tmp_path: Path) -> None:
    top = _write(tmp_path / "a.txt", "abc")
    inner = _write(tmp_path / "sub" / "b.txt", "hello")
    scan = scan_root(tmp_path)
    assert set(scan.files) == {str(top), str(inner)} and not scan.is_truncated
    assert scan.files[str(top)][1] == 3 and scan.files[str(inner)][1] == 5


def test_the_walk_never_follows_a_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside / "secret.txt")
    root = tmp_path / "root"
    real = _write(root / "real.txt")
    (root / "link_dir").symlink_to(outside, target_is_directory=True)
    (root / "link_file").symlink_to(outside / "secret.txt")
    assert set(scan_root(root).files) == {str(real)}


def test_the_walk_goes_no_deeper_than_its_bound(tmp_path: Path) -> None:
    levels = [tmp_path / "root"]
    for name in "abcdefg":  # One level more than the walk enters.
        levels.append(levels[-1] / name)
    reached = _write(levels[observe_files.MAX_SCAN_DEPTH] / "reached.txt")
    skipped = _write(levels[observe_files.MAX_SCAN_DEPTH + 1] / "skipped.txt")
    found = set(scan_root(levels[0]).files)
    assert str(reached) in found and str(skipped) not in found


def test_the_walk_stops_at_the_entry_bound_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(observe_files, "MAX_SCAN_ENTRIES", 3)
    for number in range(8):
        _write(tmp_path / f"f{number}.txt")
    scan = scan_root(tmp_path)
    assert scan.is_truncated and len(scan.files) <= 3


def test_a_root_that_is_not_there_has_no_files(tmp_path: Path) -> None:
    scan = scan_root(tmp_path / "not-here")
    assert scan.files == {} and not scan.is_truncated


def test_only_non_empty_log_files_are_read_and_the_newest_lines_kept(tmp_path: Path) -> None:
    _write(tmp_path / "old.log", "o1\no2\n", age_s=100)
    _write(tmp_path / "new.log", "n1\nn2\nn3\n")
    _write(tmp_path / "empty.log", "")
    _write(tmp_path / "notes.txt", "ignored\n")
    everything, cut = tail_logs([tmp_path], 10)
    assert [line.line for line in everything] == ["o1", "o2", "n1", "n2", "n3"] and not cut
    newest, cut = tail_logs([tmp_path], 4)  # The oldest line falls away, and that is said.
    assert [line.line for line in newest] == ["o2", "n1", "n2", "n3"] and cut


def test_a_log_line_is_cut_at_the_wire_bound(tmp_path: Path) -> None:
    _write(tmp_path / "a.log", "y" * (MAX_LOG_LINE_CHARS + 500) + "\n")
    lines, _ = tail_logs([tmp_path], 5)
    assert [len(line.line) for line in lines] == [MAX_LOG_LINE_CHARS]


def test_the_wire_cap_on_lines_applies_however_many_are_asked_for(tmp_path: Path) -> None:
    _write(tmp_path / "a.log", "".join(f"line {n}\n" for n in range(MAX_LOG_LINES + 100)))
    lines, cut = tail_logs([tmp_path], 10_000)
    assert len(lines) == MAX_LOG_LINES and cut
    assert lines[-1].line == f"line {MAX_LOG_LINES + 99}"


def test_a_read_from_mid_file_drops_the_fragment_of_its_first_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(observe_files, "TAIL_BYTES", 20)
    _write(tmp_path / "a.log", "aaaaaaaaaa\nbbbbbbbbbb\ncccccccccc\n")
    lines, _ = tail_logs([tmp_path], 10)
    assert [line.line for line in lines] == ["cccccccccc"]  # The "b" line began before the read.


def test_bytes_that_are_not_text_are_replaced_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "a.log").write_bytes(b"ok\n\xff\xfe bad\n")
    lines, _ = tail_logs([tmp_path], 10)
    assert [line.line for line in lines] == ["ok", "�� bad"]


def test_too_many_log_files_are_cut_to_the_newest_and_that_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(observe_files, "MAX_LOG_FILES", 2)
    for age, name in ((300, "a"), (200, "b"), (100, "c"), (0, "d")):
        _write(tmp_path / f"{name}.log", f"{name}\n", age_s=age)
    lines, cut = tail_logs([tmp_path], 10)
    assert [line.line for line in lines] == ["c", "d"] and cut


def test_a_log_whose_path_is_too_long_for_the_wire_is_skipped_and_reported(tmp_path: Path) -> None:
    _write(_too_long_dir(tmp_path) / "long.log", "hidden\n")
    _write(tmp_path / "short.log", "shown\n")
    lines, cut = tail_logs([tmp_path], 10)
    assert [line.line for line in lines] == ["shown"] and cut


def test_a_log_that_cannot_be_read_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "a.log", "unreadable\n")

    def refuse(_path: Path, *_args: object, **_kwargs: object) -> object:
        raise PermissionError

    monkeypatch.setattr(Path, "open", refuse)
    assert tail_logs([tmp_path], 10) == ((), False)


def test_a_root_that_is_not_there_has_no_log_lines(tmp_path: Path) -> None:
    assert tail_logs([tmp_path / "not-here"], 10) == ((), False)


def test_the_first_look_reports_only_files_changed_in_the_recent_window(tmp_path: Path) -> None:
    _write(tmp_path / "old.txt", age_s=3600)
    fresh = _write(tmp_path / "fresh.txt")
    changes, cut = ChangeWatcher().changes([tmp_path], None, 100)
    assert [(c.path, c.change) for c in changes] == [(str(fresh), FileChangeKind.MODIFIED)]
    assert not cut


def test_later_looks_tell_created_modified_and_deleted_apart(tmp_path: Path) -> None:
    watcher = ChangeWatcher()
    for name in ("a", "b", "c"):
        _write(tmp_path / f"{name}.txt", name, age_s=3600)
    watcher.changes([tmp_path], None, 100)  # The baseline.
    _write(tmp_path / "d.txt", "new")
    _write(tmp_path / "b.txt", "rewritten and longer")
    (tmp_path / "c.txt").unlink()
    changes, cut = watcher.changes([tmp_path], None, 100)
    by_name = {Path(c.path).name: c for c in changes}
    assert {n: c.change for n, c in by_name.items()} == {
        "d.txt": FileChangeKind.CREATED,
        "b.txt": FileChangeKind.MODIFIED,
        "c.txt": FileChangeKind.DELETED,
    }
    assert by_name["c.txt"].size_bytes is None and by_name["d.txt"].size_bytes == 3
    assert not cut


def test_a_cursor_limits_the_changes_to_later_ones(tmp_path: Path) -> None:
    _write(tmp_path / "ancient.txt", age_s=100)
    kept = _write(tmp_path / "recent.txt", age_s=10)
    cursor = datetime.now(UTC) - timedelta(seconds=60)
    changes, _ = ChangeWatcher().changes([tmp_path], cursor, 100)
    assert [c.path for c in changes] == [str(kept)]


def test_changes_come_oldest_first_and_a_cut_keeps_the_oldest(tmp_path: Path) -> None:
    for age, name in ((10, "newest"), (30, "oldest"), (20, "middle")):
        _write(tmp_path / f"{name}.txt", age_s=age)
    changes, cut = ChangeWatcher().changes([tmp_path], None, 2)
    # The cursor a caller takes is the last change's time, so the cut must lose the newest.
    assert [Path(c.path).name for c in changes] == ["oldest.txt", "middle.txt"] and cut


def test_a_path_too_long_for_the_wire_is_skipped_and_reported(tmp_path: Path) -> None:
    _write(_too_long_dir(tmp_path) / "long.txt")
    shown = _write(tmp_path / "short.txt")
    changes, cut = ChangeWatcher().changes([tmp_path], None, 100)
    assert [c.path for c in changes] == [str(shown)] and cut


def test_a_walk_cut_short_is_reported_with_the_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(observe_files, "MAX_SCAN_ENTRIES", 2)
    for number in range(5):
        _write(tmp_path / f"f{number}.txt")
    _, cut = ChangeWatcher().changes([tmp_path], None, 100)
    assert cut


def test_only_the_most_recent_roots_are_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(observe_files, "MAX_SNAPSHOT_ROOTS", 2)
    roots = [tmp_path / name for name in ("r1", "r2", "r3")]
    watcher = ChangeWatcher()
    for root in roots:
        _write(root / "base.txt", age_s=3600)
        watcher.changes([root], None, 100)  # r1's listing is forgotten by the third look.
    for root in (roots[0], roots[2]):
        _write(root / "new.txt")
    forgotten, _ = watcher.changes([roots[0]], None, 100)
    remembered, _ = watcher.changes([roots[2]], None, 100)
    # A root seen again with no listing can only say what is recent, not what was created.
    assert [c.change for c in forgotten] == [FileChangeKind.MODIFIED]
    assert [c.change for c in remembered] == [FileChangeKind.CREATED]
