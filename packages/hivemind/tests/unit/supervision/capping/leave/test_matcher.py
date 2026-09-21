"""Unit tests for hivemind.supervision.capping.leave.matcher: matches_leaving's glob semantics."""

from __future__ import annotations

from hivemind.cell import OsFamily
from hivemind.supervision.capping.leave.matcher import matches_leaving
from waggle.messages import PlannedLeaving


def _leaving(pattern: str, reason: str = "keep it") -> PlannedLeaving:
    return PlannedLeaving(pattern=pattern, reason=reason)


def test_matches_leaving_exact_path() -> None:
    declared = (_leaving("/home/op/config.toml"),)

    result = matches_leaving("/home/op/config.toml", declared, OsFamily.LINUX, "/home/op")

    assert result is declared[0]


def test_matches_leaving_directory_pattern_covers_nested_file() -> None:
    declared = (_leaving("~/Projects/myapp"),)

    result = matches_leaving(
        "/home/op/Projects/myapp/sub/file.txt", declared, OsFamily.LINUX, "/home/op"
    )

    assert result is declared[0]


def test_matches_leaving_directory_pattern_does_not_cover_sibling() -> None:
    declared = (_leaving("~/Projects/myapp"),)

    result = matches_leaving(
        "/home/op/Projects/other/file.txt", declared, OsFamily.LINUX, "/home/op"
    )

    assert result is None


def test_matches_leaving_tilde_expands_against_home() -> None:
    declared = (_leaving("~/keep.txt"),)

    result = matches_leaving("/home/op/keep.txt", declared, OsFamily.LINUX, "/home/op")

    assert result is declared[0]


def test_matches_leaving_single_star_within_one_segment() -> None:
    declared = (_leaving("~/logs/*.log"),)

    assert (
        matches_leaving("/home/op/logs/run.log", declared, OsFamily.LINUX, "/home/op")
        is declared[0]
    )
    # A single "*" never crosses a separator.
    assert matches_leaving("/home/op/logs/a/run.log", declared, OsFamily.LINUX, "/home/op") is None


def test_matches_leaving_double_star_spans_segments() -> None:
    declared = (_leaving("~/logs/**/*.log"),)

    result = matches_leaving("/home/op/logs/2024/jan/run.log", declared, OsFamily.LINUX, "/home/op")

    assert result is declared[0]


def test_matches_leaving_undeclared_path_returns_none() -> None:
    declared = (_leaving("~/Projects/myapp"),)

    result = matches_leaving("/home/op/other/file.txt", declared, OsFamily.LINUX, "/home/op")

    assert result is None


def test_matches_leaving_empty_declared_always_none() -> None:
    assert matches_leaving("/home/op/anything.txt", (), OsFamily.LINUX, "/home/op") is None


def test_matches_leaving_first_match_wins() -> None:
    first = _leaving("~/Projects", reason="first")
    second = _leaving("~/Projects/myapp", reason="second")

    result = matches_leaving(
        "/home/op/Projects/myapp/file.txt", (first, second), OsFamily.LINUX, "/home/op"
    )

    assert result is first


def test_matches_leaving_windows_is_case_insensitive() -> None:
    declared = (_leaving(r"~\Projects\MyApp"),)

    result = matches_leaving(
        r"C:\Users\op\projects\myapp\file.txt", declared, OsFamily.WINDOWS, r"C:\Users\op"
    )

    assert result is declared[0]


def test_matches_leaving_posix_is_case_sensitive() -> None:
    declared = (_leaving("~/Projects/MyApp"),)

    result = matches_leaving(
        "/home/op/projects/myapp/file.txt", declared, OsFamily.LINUX, "/home/op"
    )

    assert result is None
