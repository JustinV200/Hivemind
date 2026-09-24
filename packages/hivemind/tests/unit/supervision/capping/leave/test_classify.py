"""Unit tests for hivemind.supervision.capping.leave.classify: classify_path on both OS families."""

from __future__ import annotations

import pytest

from hivemind.cell import OsFamily
from hivemind.supervision.capping.leave.classify import classify_path
from hivemind.supervision.capping.leave.model import PathClass

# ──────────────────────────────────────────────────────────────────────────────
# Windows
# ──────────────────────────────────────────────────────────────────────────────


def test_classify_path_windows_home_file() -> None:
    result = classify_path(
        r"C:\Users\op\Projects\myapp\file.txt", OsFamily.WINDOWS, r"C:\Users\op", None
    )

    assert result is PathClass.HOME


def test_classify_path_windows_startup_folder_file() -> None:
    path = r"C:\Users\op\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\foo.lnk"

    result = classify_path(path, OsFamily.WINDOWS, r"C:\Users\op", None)

    assert result is PathClass.STARTUP


def test_classify_path_windows_all_users_startup_folder() -> None:
    path = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\foo.lnk"

    result = classify_path(path, OsFamily.WINDOWS, r"C:\Users\op", None)

    assert result is PathClass.STARTUP


def test_classify_path_windows_system_tree() -> None:
    assert classify_path(
        r"C:\Windows\System32\foo.dll", OsFamily.WINDOWS, r"C:\Users\op", None
    ) is (PathClass.SYSTEM)


def test_classify_path_windows_program_files() -> None:
    path = r"C:\Program Files\SomeApp\app.exe"

    assert classify_path(path, OsFamily.WINDOWS, r"C:\Users\op", None) is PathClass.SYSTEM


def test_classify_path_windows_keep_root_wins_over_home() -> None:
    result = classify_path(
        r"C:\Users\op\keep\file.txt", OsFamily.WINDOWS, r"C:\Users\op", r"C:\Users\op\keep"
    )

    assert result is PathClass.KEEP_ROOT


def test_classify_path_windows_other_drive_is_other() -> None:
    assert (
        classify_path(r"D:\data\file.txt", OsFamily.WINDOWS, r"C:\Users\op", None)
        is PathClass.OTHER
    )


def test_classify_path_windows_is_case_insensitive() -> None:
    result = classify_path(r"c:\windows\system32\foo.dll", OsFamily.WINDOWS, r"C:\Users\op", None)

    assert result is PathClass.SYSTEM


# ──────────────────────────────────────────────────────────────────────────────
# POSIX (Linux)
# ──────────────────────────────────────────────────────────────────────────────


def test_classify_path_linux_home_file() -> None:
    result = classify_path("/home/op/project/file.txt", OsFamily.LINUX, "/home/op", None)

    assert result is PathClass.HOME


def test_classify_path_linux_systemd_unit() -> None:
    path = "/etc/systemd/system/foo.service"

    assert classify_path(path, OsFamily.LINUX, "/home/op", None) is PathClass.STARTUP


def test_classify_path_linux_cron_dir() -> None:
    assert classify_path("/etc/cron.d/foo", OsFamily.LINUX, "/home/op", None) is PathClass.STARTUP


def test_classify_path_linux_xdg_autostart() -> None:
    path = "/home/op/.config/autostart/foo.desktop"

    assert classify_path(path, OsFamily.LINUX, "/home/op", None) is PathClass.STARTUP


def test_classify_path_linux_shell_rc_file() -> None:
    assert classify_path("/home/op/.bashrc", OsFamily.LINUX, "/home/op", None) is PathClass.STARTUP


def test_classify_path_linux_system_root() -> None:
    assert classify_path("/etc/passwd", OsFamily.LINUX, "/home/op", None) is PathClass.SYSTEM


def test_classify_path_linux_mount_point_is_other() -> None:
    assert classify_path("/mnt/data/file.txt", OsFamily.LINUX, "/home/op", None) is PathClass.OTHER


def test_classify_path_linux_keep_root_wins_over_startup() -> None:
    # A keep_root nested inside a startup location still wins (module docstring's own precedence).
    result = classify_path(
        "/etc/systemd/system/keep/app.conf", OsFamily.LINUX, "/home/op", "/etc/systemd/system/keep"
    )

    assert result is PathClass.KEEP_ROOT


# ──────────────────────────────────────────────────────────────────────────────
# macOS
# ──────────────────────────────────────────────────────────────────────────────


def test_classify_path_macos_launch_agents_per_user() -> None:
    path = "/home/op/Library/LaunchAgents/foo.plist"

    assert classify_path(path, OsFamily.MACOS, "/home/op", None) is PathClass.STARTUP


def test_classify_path_macos_launch_daemons_system() -> None:
    assert classify_path("/Library/LaunchDaemons/foo.plist", OsFamily.MACOS, "/home/op", None) is (
        PathClass.STARTUP
    )


def test_classify_path_macos_system_root() -> None:
    assert (
        classify_path("/Applications/Foo.app", OsFamily.MACOS, "/home/op", None) is PathClass.SYSTEM
    )


@pytest.mark.parametrize("os_family", list(OsFamily))
def test_classify_path_home_itself_is_home(os_family: OsFamily) -> None:
    """The home directory itself, not just something under it, classifies as HOME."""
    home = r"C:\Users\op" if os_family is OsFamily.WINDOWS else "/home/op"

    assert classify_path(home, os_family, home, None) is PathClass.HOME
