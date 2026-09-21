"""Unit tests for hivemind.supervision.capping.leave.executable: looks_executable."""

from __future__ import annotations

from hivemind.cell import OsFamily
from hivemind.supervision.capping.leave.executable import looks_executable


def test_looks_executable_windows_exe_suffix() -> None:
    assert looks_executable(r"C:\Users\op\tool.exe", OsFamily.WINDOWS, b"binary") is True


def test_looks_executable_windows_ps1_suffix() -> None:
    assert looks_executable(r"C:\Users\op\script.ps1", OsFamily.WINDOWS, b"Write-Host hi") is True


def test_looks_executable_windows_txt_suffix_is_not_executable() -> None:
    assert looks_executable(r"C:\Users\op\notes.txt", OsFamily.WINDOWS, b"hello") is False


def test_looks_executable_windows_shebang_does_not_count() -> None:
    # Windows never treats a shebang as meaningful (module docstring's own invariant).
    assert looks_executable(r"C:\Users\op\notes", OsFamily.WINDOWS, b"#!/bin/sh\necho hi") is False


def test_looks_executable_posix_sh_suffix() -> None:
    assert looks_executable("/home/op/run.sh", OsFamily.LINUX, b"echo hi") is True


def test_looks_executable_posix_shebang_no_suffix() -> None:
    assert looks_executable("/home/op/run", OsFamily.LINUX, b"#!/bin/bash\necho hi") is True


def test_looks_executable_posix_plain_text_is_not_executable() -> None:
    assert looks_executable("/home/op/notes.txt", OsFamily.LINUX, b"just some notes") is False


def test_looks_executable_is_case_insensitive() -> None:
    assert looks_executable(r"C:\Users\op\TOOL.EXE", OsFamily.WINDOWS, b"binary") is True
