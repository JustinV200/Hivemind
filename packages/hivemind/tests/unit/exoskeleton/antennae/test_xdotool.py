"""Unit tests for hivemind.exoskeleton.antennae.xdotool: the commands XdotoolAntennae runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import CompletedCommand, ExecSpec
from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.antennae.xdotool import INPUT_TIMEOUT_S, XdotoolAntennae
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Point, ScreenSize
from hivemind.exoskeleton.x11 import X11Display
from waggle.clock import FakeClock
from waggle.messages.capping import MouseButton

_DISPLAY = X11Display(name=":2", authority=None, size=ScreenSize(100, 50))
_ORIGIN = Point(0, 0)  # Where the pointer reads back unless a test puts it elsewhere.


def _antennae(
    tmp_path: Path, seen: list[ExecSpec], stdout: bytes = b"", at: Point = _ORIGIN
) -> XdotoolAntennae:
    """XdotoolAntennae over a FakeSession: every command prints `stdout`, or the pointer at `at`."""
    location = f"X={at.x}\nY={at.y}\n".encode()

    def respond(spec: ExecSpec) -> CompletedCommand:
        seen.append(spec)
        # An explicit `stdout` is what every command prints, a pointer read's included.
        printed = location if not stdout and "getmouselocation" in spec.argv else stdout
        return CompletedCommand(exit_code=0, stdout=printed, stderr=b"", duration_s=0.0)

    session = FakeSession(tmp_path, FakeClock(), responder=respond)
    return XdotoolAntennae(session, _DISPLAY)


async def test_move_waits_for_the_pointer_to_arrive(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []

    await _antennae(tmp_path, seen).move(Point(3, 4))

    assert seen[0].argv[1] == "getmouselocation"  # Where it is decides whether to wait.
    assert seen[1].argv == ("xdotool", "mousemove", "--sync", "3", "4")
    assert seen[1].env == {"DISPLAY": ":2"}


async def test_a_move_or_click_where_the_pointer_already_is_never_waits(tmp_path: Path) -> None:
    # A synced move to the pointer's own position hangs xdotool until its timeout.
    seen: list[ExecSpec] = []
    antennae = _antennae(tmp_path, seen, at=Point(9, 8))

    await antennae.move(Point(9, 8))
    await antennae.click(Point(9, 8), MouseButton.LEFT)

    moves = [spec.argv for spec in seen if spec.argv[1] == "mousemove"]
    assert moves[0] == ("xdotool", "mousemove", "9", "8")
    assert moves[1][:4] == ("xdotool", "mousemove", "9", "8")
    assert all("--sync" not in argv for argv in moves)


@pytest.mark.parametrize(
    ("button", "count", "number"),
    [(MouseButton.LEFT, 1, "1"), (MouseButton.MIDDLE, 1, "2"), (MouseButton.RIGHT, 2, "3")],
)
async def test_click_moves_then_clicks_the_x_button(
    tmp_path: Path, button: MouseButton, count: int, number: str
) -> None:
    seen: list[ExecSpec] = []

    await _antennae(tmp_path, seen).click(Point(9, 8), button, count)

    argv = seen[1].argv
    assert argv[:5] == ("xdotool", "mousemove", "--sync", "9", "8")
    assert argv[5:8] == ("click", "--repeat", str(count))
    assert argv[-1] == number


async def test_an_input_off_the_screen_or_a_bad_count_runs_nothing(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []
    antennae = _antennae(tmp_path, seen)

    with pytest.raises(PeripheralError, match="off the 100x50 screen"):
        await antennae.click(Point(100, 0), MouseButton.LEFT)
    with pytest.raises(PeripheralError, match="count must be 1 or 2"):
        await antennae.click(Point(1, 1), MouseButton.LEFT, 3)
    assert seen == []


async def test_typed_text_is_one_argument_after_the_end_of_options(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []
    text = "-v; $(rm -rf /)"

    await _antennae(tmp_path, seen).type_text(text)

    assert seen[0].argv[-2:] == ("--", text)
    assert seen[0].timeout_s > INPUT_TIMEOUT_S  # Grows with the text so pacing never trips it.


async def test_scroll_clicks_the_wheel_buttons_for_each_axis(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []

    await _antennae(tmp_path, seen).scroll(dx=-2, dy=3)

    assert [spec.argv[1:] for spec in seen] == [
        ("click", "--repeat", "3", "5"),
        ("click", "--repeat", "2", "6"),
    ]


async def test_press_clears_held_modifiers_first(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []

    await _antennae(tmp_path, seen).press("ctrl+s")

    assert seen[0].argv == ("xdotool", "key", "--clearmodifiers", "ctrl+s")


async def test_pointer_parses_the_shell_form(tmp_path: Path) -> None:
    antennae = _antennae(tmp_path, [], stdout=b"X=17\nY=23\nSCREEN=0\nWINDOW=5\n")

    assert await antennae.pointer() == Point(17, 23)


async def test_pointer_output_without_coordinates_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(PeripheralError, match="no X= and Y="):
        await _antennae(tmp_path, [], stdout=b"garbage").pointer()
