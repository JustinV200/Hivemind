"""Unit tests for hivemind.supervision.capping.diff: apply_unified_diff's three hunk shapes."""

from __future__ import annotations

import pytest

from hivemind.supervision.capping.diff import apply_unified_diff
from hivemind.supervision.capping.errors import DiffApplyError


def test_apply_unified_diff_new_file_hunk() -> None:
    diff_text = "@@ -0,0 +1,2 @@\n+line one\n+line two\n"

    result = apply_unified_diff(None, diff_text)

    assert result == b"line one\nline two"


def test_apply_unified_diff_whole_file_replacement_hunk() -> None:
    prior = b"old one\nold two\nold three"
    diff_text = "@@ -1,3 +1,2 @@\n-old one\n-old two\n-old three\n+new one\n+new two\n"

    result = apply_unified_diff(prior, diff_text)

    assert result == b"new one\nnew two"


def test_apply_unified_diff_ordinary_context_hunk() -> None:
    prior = b"A\nB\nC\nD\nE"
    diff_text = "@@ -2,3 +2,3 @@\n B\n-C\n+C2\n D\n"

    result = apply_unified_diff(prior, diff_text)

    assert result == b"A\nB\nC2\nD\nE"


def test_apply_unified_diff_skips_file_header_lines() -> None:
    prior = b"one"
    diff_text = "--- a/note.txt\n+++ b/note.txt\n@@ -1,1 +1,1 @@\n-one\n+two\n"

    result = apply_unified_diff(prior, diff_text)

    assert result == b"two"


def test_apply_unified_diff_raises_on_a_context_mismatch() -> None:
    prior = b"A\nB\nC"
    diff_text = "@@ -1,3 +1,3 @@\n A\n-NOT_B\n+B2\n C\n"

    with pytest.raises(DiffApplyError, match="mismatch"):
        apply_unified_diff(prior, diff_text, path="note.txt")


def test_apply_unified_diff_raises_when_there_are_no_hunks() -> None:
    with pytest.raises(DiffApplyError, match="no hunks"):
        apply_unified_diff(b"content", "not a diff at all", path="note.txt")


def test_apply_unified_diff_error_names_the_path() -> None:
    with pytest.raises(DiffApplyError) as excinfo:
        apply_unified_diff(None, "garbage", path="src/thing.py")

    assert "src/thing.py" in str(excinfo.value)
