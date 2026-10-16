"""Tests for hivemind.wardens.watch.probe.files: log lines and file changes from the scripts' text.

Fits into the Hive:
    Mirrors src/hivemind/wardens/watch/probe/files.py (codingrules section 3). The listing
    records are in the exact shape GNU find prints them; hypothesis feeds the parsers text that is
    nothing like it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.watch.probe.files for the module under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from hivemind.wardens.watch.probe.commands import (
    MAX_LOG_FILES,
    RECENT_WINDOW_S,
    TAIL_BYTES,
    TAIL_LINES,
)
from hivemind.wardens.watch.probe.files import (
    MAX_LISTED_FILES,
    MAX_SNAPSHOT_ROOTS,
    ChangeTracker,
    RootListing,
    newest_logs,
    parse_listing,
    parse_roots,
    parse_tail,
)
from waggle.messages.swarm import FileChangeKind
from waggle.messages.swarm.watch import MAX_LOG_LINE_CHARS, MAX_LOG_LINES, MAX_OBSERVED_PATH_CHARS

_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
_EPOCH = _NOW.timestamp()


def _record(path: str, mtime: float = _EPOCH, size: int = 10) -> str:
    """One NUL-terminated record: mtime, size and path, as GNU find prints them."""
    return f"{mtime:.10f}\t{size}\t{path}\0"


def _listing(*files: tuple[str, float, int], truncated: bool = False) -> RootListing:
    """A RootListing of (path, mtime, size) files."""
    return RootListing({path: (mtime, size) for path, mtime, size in files}, truncated)


# ──────────────────────────────────────────────────────────────────────────────
# parse_listing and parse_roots
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_listing_reads_the_recorded_find_records() -> None:
    # Recorded from GNU find; the root was a temporary directory, renamed here.
    text = (
        "1791945518.3675462700\t1\t/srv/rec-5zj998er/b.txt\0"
        "1791945518.3674817680\t23\t/srv/rec-5zj998er/a.log\0"
    )

    listing = parse_listing(text)

    assert listing == RootListing(
        {
            "/srv/rec-5zj998er/b.txt": (float("1791945518.3675462700"), 1),
            "/srv/rec-5zj998er/a.log": (float("1791945518.3674817680"), 23),
        },
        False,
    )


def test_a_path_may_hold_a_newline_and_a_tab() -> None:
    listing = parse_listing(_record("/r/line\nbreak\tand tab.txt"))

    assert list(listing.files) == ["/r/line\nbreak\tand tab.txt"]


def test_a_record_cut_off_by_head_is_dropped_and_the_listing_is_truncated() -> None:
    listing = parse_listing(_record("/r/whole.txt") + "1791945518.3\t12\t/r/par")

    assert list(listing.files) == ["/r/whole.txt"]
    assert listing.is_truncated is True


def test_a_complete_listing_is_not_truncated() -> None:
    assert parse_listing(_record("/r/a") + _record("/r/b")).is_truncated is False
    assert parse_listing("") == RootListing({}, False)


def test_a_path_over_the_wire_bound_is_skipped_and_the_listing_says_so() -> None:
    too_long = "/r/" + "x" * MAX_OBSERVED_PATH_CHARS

    listing = parse_listing(_record(too_long) + _record("/r/ok"))

    assert list(listing.files) == ["/r/ok"]
    assert listing.is_truncated is True


def test_a_path_that_was_not_text_is_skipped() -> None:
    listing = parse_listing(_record("/r/bad�name") + _record("/r/ok"))

    assert list(listing.files) == ["/r/ok"]
    assert listing.is_truncated is True


def test_a_record_that_is_not_a_file_is_ignored_without_marking_the_listing() -> None:
    text = (
        "nan\t5\t/r/nan\0inf\t5\t/r/inf\0-1\t5\t/r/neg\0999999999999999\t5\t/r/future\0"
        "1.0\t-5\t/r/size\0abc\t5\t/r/abc\0\t\t\0no tabs\0"
        "1.0\t5\trelative/path\0" + _record("/r/ok")
    )

    listing = parse_listing(text)

    assert list(listing.files) == ["/r/ok"]
    assert listing.is_truncated is False


def test_a_listing_keeps_at_most_the_bound_and_one_more_proves_there_was_more() -> None:
    text = "".join(_record(f"/r/{n}") for n in range(MAX_LISTED_FILES + 1))

    listing = parse_listing(text)

    assert len(listing.files) == MAX_LISTED_FILES
    assert listing.is_truncated is True
    assert not parse_listing(text[: -len(_record(f"/r/{MAX_LISTED_FILES}"))]).is_truncated


def test_parse_roots_reads_only_the_roots_whose_section_was_printed() -> None:
    sections = {"root1": _record("/a/f"), "root3": ""}

    listings = parse_roots(sections, ["/a", "/b", "/c"])

    assert set(listings) == {"/a", "/c"}  # /b was never reached: not looked at, not empty.
    assert list(listings["/a"].files) == ["/a/f"]
    assert listings["/c"].files == {}


@given(st.text())
def test_parse_listing_never_raises_and_only_returns_paths_the_wire_can_carry(text: str) -> None:
    listing = parse_listing(text)

    assert len(listing.files) <= MAX_LISTED_FILES
    for path, (mtime, size) in listing.files.items():
        assert path.startswith("/")
        assert len(path) <= MAX_OBSERVED_PATH_CHARS
        assert 0 <= mtime < 253_402_300_799.0
        assert size >= 0


# ──────────────────────────────────────────────────────────────────────────────
# newest_logs
# ──────────────────────────────────────────────────────────────────────────────


def test_newest_logs_picks_non_empty_log_files_oldest_first() -> None:
    listing = _listing(
        ("/r/new.log", 300.0, 5),
        ("/r/old.log", 100.0, 7),
        ("/r/empty.log", 400.0, 0),
        ("/r/notes.txt", 500.0, 9),
        ("/r/mid.log", 200.0, 3),
    )

    logs, cut = newest_logs([listing])

    assert logs == [("/r/old.log", 7), ("/r/mid.log", 3), ("/r/new.log", 5)]
    assert cut is False


def test_newest_logs_keeps_only_the_newest_few_and_says_it_left_some_out() -> None:
    listing = _listing(*((f"/r/{n:02d}.log", float(n), 1) for n in range(MAX_LOG_FILES + 5)))

    logs, cut = newest_logs([listing])

    assert len(logs) == MAX_LOG_FILES
    assert logs[-1][0] == f"/r/{MAX_LOG_FILES + 4:02d}.log"
    assert cut is True


def test_a_log_under_two_nested_roots_is_counted_once() -> None:
    outer = _listing(("/r/sub/a.log", 1.0, 1))
    inner = _listing(("/r/sub/a.log", 1.0, 1))

    assert newest_logs([outer, inner])[0] == [("/r/sub/a.log", 1)]


# ──────────────────────────────────────────────────────────────────────────────
# parse_tail
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_tail_labels_lines_with_the_path_the_probe_asked_for() -> None:
    sections = {"tail1": "one\ntwo\n", "tail2": "three\n"}

    lines, cut = parse_tail(sections, [("/r/a.log", 10), ("/r/b.log", 6)], 50)

    assert [(line.path, line.line) for line in lines] == [
        ("/r/a.log", "one"),
        ("/r/a.log", "two"),
        ("/r/b.log", "three"),
    ]
    assert cut is False


def test_the_first_line_of_a_file_read_from_the_middle_is_a_fragment_and_is_dropped() -> None:
    sections = {"tail1": "lf of a long line\nwhole line\n"}

    big, _ = parse_tail(sections, [("/r/a.log", TAIL_BYTES + 1)], 50)
    small, _ = parse_tail(sections, [("/r/a.log", TAIL_BYTES)], 50)

    assert [line.line for line in big] == ["whole line"]
    assert [line.line for line in small] == ["lf of a long line", "whole line"]


def test_blank_lines_and_carriage_returns_are_not_lines_of_a_log() -> None:
    sections = {"tail1": "a\r\n\r\n   \nb\r\n"}

    lines, _ = parse_tail(sections, [("/r/a.log", 5)], 50)

    assert [line.line for line in lines] == ["a", "b"]


def test_a_line_over_the_wire_bound_is_cut_not_refused() -> None:
    lines, _ = parse_tail({"tail1": "y" * (MAX_LOG_LINE_CHARS + 50)}, [("/r/a.log", 5)], 50)

    assert len(lines[0].line) == MAX_LOG_LINE_CHARS


def test_each_file_keeps_its_newest_lines_and_the_cut_is_reported() -> None:
    text = "\n".join(f"line {n}" for n in range(TAIL_LINES + 4)) + "\n"

    lines, cut = parse_tail({"tail1": text}, [("/r/a.log", 5)], 500)

    assert len(lines) == TAIL_LINES
    assert lines[-1].line == f"line {TAIL_LINES + 3}"
    assert cut is True


def test_the_whole_answer_keeps_the_newest_lines_up_to_the_cap() -> None:
    sections = {"tail1": "a1\na2\na3\n", "tail2": "b1\nb2\nb3\n"}

    lines, cut = parse_tail(sections, [("/r/a.log", 5), ("/r/b.log", 5)], 4)

    assert [line.line for line in lines] == ["a3", "b1", "b2", "b3"]
    assert cut is True


def test_the_cap_never_exceeds_the_wire_bound() -> None:
    text = "\n".join(f"l{n}" for n in range(TAIL_LINES)) + "\n"
    sections = {f"tail{n}": text for n in range(1, MAX_LOG_FILES + 1)}
    logs = [(f"/r/{n}.log", 5) for n in range(1, MAX_LOG_FILES + 1)]

    lines, cut = parse_tail(sections, logs, 10_000)

    assert len(lines) == MAX_LOG_LINES
    assert cut is True


def test_a_missing_section_is_a_file_with_no_lines() -> None:
    lines, _ = parse_tail({"tail2": "x\n"}, [("/r/a.log", 5), ("/r/b.log", 5)], 50)

    assert [(line.path, line.line) for line in lines] == [("/r/b.log", "x")]


# ──────────────────────────────────────────────────────────────────────────────
# ChangeTracker
# ──────────────────────────────────────────────────────────────────────────────


def _changes(
    tracker: ChangeTracker, listings: dict[str, RootListing], since: datetime | None = None
) -> list[tuple[str, FileChangeKind]]:
    """Run one look at `_NOW` and return (path, kind) pairs, oldest change first."""
    found, _ = tracker.changes(listings, since, _NOW, 500)
    return [(change.path, change.change) for change in found]


def test_the_first_look_reports_only_files_changed_in_the_recent_window() -> None:
    fresh = _EPOCH - 10
    stale = _EPOCH - RECENT_WINDOW_S - 60
    listings = {"/r": _listing(("/r/fresh", fresh, 1), ("/r/stale", stale, 1))}

    assert _changes(ChangeTracker(), listings) == [("/r/fresh", FileChangeKind.MODIFIED)]


def test_a_later_look_tells_created_from_modified_and_sees_deletions() -> None:
    tracker = ChangeTracker()
    before = _listing(("/r/keep", 1.0, 1), ("/r/edit", 1.0, 1), ("/r/gone", 1.0, 1))
    _changes(tracker, {"/r": before})
    after = _listing(("/r/keep", 1.0, 1), ("/r/edit", 2.0, 5), ("/r/new", 3.0, 2))

    found, cut = tracker.changes({"/r": after}, None, _NOW, 500)

    kinds = {change.path: change for change in found}
    assert set(kinds) == {"/r/edit", "/r/new", "/r/gone"}
    assert kinds["/r/edit"].change is FileChangeKind.MODIFIED
    assert kinds["/r/edit"].size_bytes == 5
    assert kinds["/r/new"].change is FileChangeKind.CREATED
    assert (kinds["/r/gone"].change, kinds["/r/gone"].size_bytes) == (FileChangeKind.DELETED, None)
    assert kinds["/r/gone"].at == _NOW
    assert cut is False


def test_a_look_with_nothing_different_reports_nothing() -> None:
    tracker = ChangeTracker()
    listing = {"/r": _listing(("/r/a", 1.0, 1))}
    _changes(tracker, listing)

    assert _changes(tracker, listing) == []


def test_changes_come_oldest_first() -> None:
    tracker = ChangeTracker()
    _changes(tracker, {"/r": _listing()})
    after = _listing(("/r/late", 30.0, 1), ("/r/early", 10.0, 1), ("/r/mid", 20.0, 1))

    assert [path for path, _ in _changes(tracker, {"/r": after})] == [
        "/r/early",
        "/r/mid",
        "/r/late",
    ]


def test_a_cursor_hides_changes_at_or_before_it_but_not_deletions() -> None:
    tracker = ChangeTracker()
    _changes(tracker, {"/r": _listing(("/r/gone", 1.0, 1), ("/r/old", 1.0, 1))})
    after = _listing(("/r/old", 2.0, 1), ("/r/newer", 9.0, 1))
    cursor = datetime.fromtimestamp(5.0, UTC)

    found = _changes(tracker, {"/r": after}, since=cursor)

    assert found == [("/r/newer", FileChangeKind.CREATED), ("/r/gone", FileChangeKind.DELETED)]


def test_a_file_under_two_nested_roots_is_reported_once() -> None:
    tracker = ChangeTracker()
    both = {
        "/r": _listing(("/r/sub/f", _EPOCH - 5, 1)),
        "/r/sub": _listing(("/r/sub/f", _EPOCH - 5, 1)),
    }

    assert _changes(tracker, both) == [("/r/sub/f", FileChangeKind.MODIFIED)]


def test_a_cap_keeps_the_oldest_changes_so_the_cursor_loses_nothing() -> None:
    tracker = ChangeTracker()
    _changes(tracker, {"/r": _listing()})
    after = _listing(*((f"/r/{n}", float(n + 1), 1) for n in range(5)))

    found, cut = tracker.changes({"/r": after}, None, _NOW, 3)

    assert [change.path for change in found] == ["/r/0", "/r/1", "/r/2"]
    assert cut is True


def test_what_the_cap_left_out_comes_out_on_the_next_look() -> None:
    tracker = ChangeTracker()
    _changes(tracker, {"/r": _listing(("/r/gone", 1.0, 1))})
    after = _listing(*((f"/r/{n}", float(n + 1), 1) for n in range(5)))

    first, _ = tracker.changes({"/r": after}, None, _NOW, 3)
    second, cut = tracker.changes({"/r": after}, first[-1].at, _NOW, 3)
    third, last = tracker.changes({"/r": after}, second[-1].at, _NOW, 3)

    assert [c.path for c in first] == ["/r/0", "/r/1", "/r/2"]
    assert [(c.path, c.change) for c in second] == [
        ("/r/3", FileChangeKind.CREATED),
        ("/r/4", FileChangeKind.CREATED),
        ("/r/gone", FileChangeKind.DELETED),  # Stamped `now`, so last; left out twice, not lost.
    ]
    assert cut is False and third == () and last is False


def test_a_change_left_out_under_one_root_is_not_absorbed_through_a_nested_one() -> None:
    tracker = ChangeTracker()
    both = {"/r": _listing(), "/r/sub": _listing()}
    _changes(tracker, both)
    after = {
        "/r": _listing(("/r/a", 1.0, 1), ("/r/sub/f", 2.0, 1)),
        "/r/sub": _listing(("/r/sub/f", 2.0, 1)),
    }

    first, _ = tracker.changes(after, None, _NOW, 1)
    second, _ = tracker.changes(after, first[-1].at, _NOW, 5)

    assert [c.path for c in first] == ["/r/a"]
    assert [c.path for c in second] == ["/r/sub/f"]  # Once, from whichever root lists it first.


def test_a_truncated_listing_makes_the_answer_a_lower_bound() -> None:
    _, cut = ChangeTracker().changes({"/r": _listing(truncated=True)}, None, _NOW, 500)

    assert cut is True


def test_a_root_that_was_not_listed_keeps_its_last_look_and_is_never_read_as_all_deleted() -> None:
    tracker = ChangeTracker()
    _changes(tracker, {"/a": _listing(("/a/f", 1.0, 1)), "/b": _listing(("/b/f", 1.0, 1))})

    skipped = _changes(tracker, {"/b": _listing(("/b/f", 1.0, 1))})  # /a's section never printed.
    back = _changes(tracker, {"/a": _listing(("/a/f", 1.0, 1))})

    assert skipped == []
    assert back == []


def test_the_tracker_remembers_a_bounded_number_of_roots() -> None:
    tracker = ChangeTracker()
    roots = [f"/r{n}" for n in range(MAX_SNAPSHOT_ROOTS + 3)]
    for root in roots:
        _changes(tracker, {root: _listing((f"{root}/f", 1.0, 1))})

    forgotten = _changes(tracker, {roots[0]: _listing((f"{roots[0]}/f", _EPOCH - 1, 1))})
    kept = _changes(tracker, {roots[-1]: _listing()})

    assert forgotten == [(f"{roots[0]}/f", FileChangeKind.MODIFIED)]  # Looked at like a new root.
    assert kept == [(f"{roots[-1]}/f", FileChangeKind.DELETED)]  # Remembered, so it was deleted.


def test_a_window_start_is_five_minutes_before_now() -> None:
    assert timedelta(seconds=RECENT_WINDOW_S) == timedelta(minutes=5)
