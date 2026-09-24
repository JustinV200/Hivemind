"""Unit tests for hivemind.guard.file_urls: whether a file URL stays inside a lease's roots.

Fits into the Hive:
    Mirrors src/hivemind/guard/file_urls.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.file_urls for the module under test.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hivemind.guard import file_url_escapes, file_url_path, is_file_url, is_within

# One lease's scratch on a Linux Cell, as attach names it. A pure path, so every host running these
# tests reads it as the Cell does, whatever that host's own OS (hivemind.guard.file_urls).
_ROOTS = (PurePosixPath("/srv/lease/scratch"),)
# The same lease on a Windows Cell: compared case-insensitively, with backslashes as separators.
_WINDOWS_ROOTS = (PureWindowsPath("C:/lease/scratch"),)


@pytest.mark.parametrize(
    "url",
    [
        "file:///srv/lease/scratch/page.html",
        "file:///srv/lease/scratch",
        "file:///srv/lease/scratch/site/./login.html",
        "file:///srv/lease/scratch/site/../page.html",
        "file://localhost/srv/lease/scratch/page.html",
        "FILE:///srv/lease/scratch/page.html",
        "file:///srv/lease/scratch/page.html?next=1#top",
        "file:///srv/lease/scratch/my%20page.html",
    ],
)
def test_a_file_url_inside_scratch_stays(url: str) -> None:
    assert not file_url_escapes(url, _ROOTS)


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("file:///home/bee/.ssh/id_rsa", "outside every root"),
        ("file:///srv/lease/scratch/../other/x", "a dot segment leaves scratch"),
        ("file:///srv/lease/scratch/%2E%2E/other/x", "an encoded dot segment"),
        ("file:///srv/lease/scratch/%2e%2E/other/x", "mixed-case encoding"),
        ("file:///srv/lease/scratch\\..\\other\\x", "backslashes are separators"),
        ("file:///srv/lease/scratch/x%2F..%2F..%2Fother", "an encoded slash"),
        ("file:///srv/lease/scratchy/x", "a sibling sharing a prefix"),
        ("file://fileserver/srv/lease/scratch/x", "another host's share"),
        ("file:srv/lease/scratch/x", "a relative path"),
        ("file:///srv/lease/scratch/a%00b", "a NUL byte"),
        ("file:////srv/lease/scratch/x", "a leading double slash"),
    ],
)
def test_a_file_url_that_leaves_scratch_escapes(url: str, why: str) -> None:
    assert file_url_escapes(url, _ROOTS), why


@pytest.mark.parametrize(
    "url", ["https://example.test/", "http://127.0.0.1:8000/x", "about:blank", "chrome://x"]
)
def test_a_url_that_is_not_a_file_url_never_escapes(url: str) -> None:
    assert not is_file_url(url)
    assert not file_url_escapes(url, ())


def test_no_roots_lets_no_file_url_stay() -> None:
    assert file_url_escapes("file:///srv/lease/scratch/page.html", ())


def test_file_url_path_normalises_what_the_browser_would_collapse() -> None:
    path = file_url_path("file:///srv/lease/scratch/a/./b/../c%20d.html")

    assert path == PurePosixPath("/srv/lease/scratch/a/c d.html")


def test_file_url_path_is_none_for_another_host_or_scheme() -> None:
    assert file_url_path("file://fileserver/x") is None
    assert file_url_path("https://example.test/x") is None


def test_is_within_normalises_its_roots_too() -> None:
    assert is_within(
        PurePosixPath("/srv/lease/scratch/x"), (PurePosixPath("/srv/lease/./scratch/"),)
    )
    relative = (PurePosixPath("scratch"),)
    assert not is_within(PurePosixPath("/srv/lease/scratch/x"), relative)  # A relative root.


@given(st.lists(st.sampled_from(["a", "..", ".", "b", "%2E%2E", "c\\..", "%2F"]), max_size=8))
def test_a_file_url_that_stays_never_resolves_outside_scratch(segments: list[str]) -> None:
    # Whatever the segments, a URL judged inside must name a path under scratch once collapsed.
    url = "file:///srv/lease/scratch/" + "/".join(segments)

    if not file_url_escapes(url, _ROOTS):
        path = file_url_path(url)
        assert path is not None
        assert path == _ROOTS[0] or _ROOTS[0] in path.parents


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/lease/scratch/page.html",
        "file:///c:/LEASE/Scratch/page.html",
        "file:///C|/lease/scratch/page.html",
        "file:C:/lease/scratch/page.html",
        "file:///C:/lease/scratch/site/../page.html",
    ],
)
def test_a_file_url_inside_a_windows_cells_scratch_stays(url: str) -> None:
    # Drive letters, either case, the old "C|" spelling and Chromium's "file:C:/" all read as the
    # Windows path they name, compared the way a Windows filesystem compares names.
    assert not file_url_escapes(url, _WINDOWS_ROOTS)


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("file:///C:/lease/scratch/%5C..%5C..%5Csecret", "an encoded backslash is a separator"),
        ("file:///C:/lease/scratch/../secret", "a dot segment leaves scratch"),
        ("file:///D:/lease/scratch/page.html", "another drive"),
        ("file:///lease/scratch/page.html", "no drive: not a Windows path at all"),
        ("file:///C:", "a drive with no path"),
    ],
)
def test_a_file_url_that_leaves_a_windows_cells_scratch_escapes(url: str, why: str) -> None:
    assert file_url_escapes(url, _WINDOWS_ROOTS), why


def test_a_linux_cells_root_is_read_as_posix_from_a_windows_host() -> None:
    # On a Windows host, Path("/srv/lease/scratch") is a drive-less WindowsPath; it still names a
    # Linux Cell's directory, so it is compared the POSIX way: exactly, case and all.
    from_windows = (PureWindowsPath("/srv/lease/scratch"),)

    assert not file_url_escapes("file:///srv/lease/scratch/page.html", from_windows)
    assert file_url_escapes("file:///srv/lease/Scratch/page.html", from_windows)
    assert file_url_escapes("file:///srv/lease/scratch/../other", from_windows)


def test_an_encoded_backslash_is_a_name_character_on_a_posix_cell() -> None:
    # A POSIX file name may hold a backslash; only a literal one in the URL is a separator.
    assert not file_url_escapes("file:///srv/lease/scratch/%5C..%5C..%5Csecret", _ROOTS)


def test_the_two_flavours_never_contain_each_other() -> None:
    assert file_url_escapes("file:///C:/lease/scratch/page.html", _ROOTS)
    assert not is_within(PurePosixPath("/lease/scratch/x"), _WINDOWS_ROOTS)
