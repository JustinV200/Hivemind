"""Unit tests for hivemind.exoskeleton.scratch: ScratchLayout paths and environment."""

from __future__ import annotations

from pathlib import Path

from hivemind.exoskeleton.scratch import EXOSKELETON_DIR, MAX_SOCKET_PATH_BYTES, ScratchLayout


def test_every_path_lives_under_the_exoskeleton_directory_in_scratch() -> None:
    layout = ScratchLayout.under(Path("/scratch/lease"))
    paths = (*layout.directories(), layout.authority, layout.pulse_socket, layout.pulse_cookie)

    assert layout.root == Path("/scratch/lease") / EXOSKELETON_DIR
    assert all(layout.root in path.parents for path in paths)


def test_home_environment_points_home_and_every_xdg_directory_into_scratch() -> None:
    layout = ScratchLayout.under(Path("/scratch/lease"))

    env = layout.home_environment()

    assert set(env) == {
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
    }
    assert all(value.startswith(str(layout.root)) for value in env.values())
    assert env["HOME"] == str(layout.home)
    assert layout.authority == layout.home / ".Xauthority"  # Where X clients look under HOME.


def test_socket_fits_only_within_a_unix_socket_address() -> None:
    short = ScratchLayout.under(Path("/s"))
    deep = ScratchLayout.under(Path("/" + "d" * MAX_SOCKET_PATH_BYTES))

    assert short.socket_fits()
    assert not deep.socket_fits()
