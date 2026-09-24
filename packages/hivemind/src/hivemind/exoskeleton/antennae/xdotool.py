"""Define XdotoolAntennae: the Antennae over an X display, driving input with xdotool.

The Antennae are the Exoskeleton's input peripheral (`hivemind.exoskeleton.antennae.base`). This
backend runs `xdotool` on the Cell, through its `CellSession`, against one X display: `mousemove
--sync` so a click lands only once the pointer is really there, `click` with the X button number
(4 to 7 are the wheel, which is how X scrolls), `type` with a small per-key delay and `--` before
the text so text starting with a dash is typed rather than parsed as an option, `key
--clearmodifiers` for a chord, and `getmouselocation --shell` to read the pointer back. The display
needs a window manager running for pointer moves to take effect at all (ADR-0031); attach
guarantees one, and proves it by moving the pointer before handing this backend out.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`. Built
    by `hivemind.exoskeleton.attach`; called by the Capping gate's GUI surface applying a GUI
    proposal. Calls into `hivemind.cell` (CellSession), `hivemind.exoskeleton.commands`, `.errors`,
    `.geometry`, `.x11` and waggle's MouseButton only.

Key invariants:
    - A point off the screen is refused before any command runs.
    - Text is one argument after `--`, never shell-interpreted, and never appears in an error.
    - Every command runs with a timeout; typing's grows with the text so pacing never trips it.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the xdotool backend.
    - hivemind.exoskeleton.antennae.base for the Antennae contract.
"""

from __future__ import annotations

import re

from hivemind.cell import CellSession
from hivemind.exoskeleton.commands import PeripheralCommand, run_peripheral
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Point
from hivemind.exoskeleton.x11 import X11Display
from waggle.messages.capping import MouseButton

INPUT_TIMEOUT_S = 10.0  # One move, click, chord or scroll; ten seconds is a hung display.
TYPE_DELAY_MS = 12  # xdotool's own default per-key delay: fast, and applications keep up.
_TYPE_SECONDS_PER_KEY = 0.05  # Timeout budget per typed key: generous against the delay above.
_CLICK_GAP_MS = 80  # Between the two clicks of a double click: well inside any double-click window.
_PERIPHERAL = "antennae"  # How this backend names itself in a PeripheralError.
_BUTTONS = {MouseButton.LEFT: "1", MouseButton.MIDDLE: "2", MouseButton.RIGHT: "3"}
# X scrolls with wheel "buttons": 4 up, 5 down, 6 left, 7 right, one click per step.
_WHEEL_UP, _WHEEL_DOWN, _WHEEL_LEFT, _WHEEL_RIGHT = "4", "5", "6", "7"
_LOCATION = re.compile(r"^(X|Y)=(\d+)$", re.MULTILINE)  # getmouselocation --shell's two lines.

__all__ = ["INPUT_TIMEOUT_S", "TYPE_DELAY_MS", "XdotoolAntennae"]


class XdotoolAntennae:
    """Drive one X display's pointer and keyboard with xdotool, through the Cell's session."""

    def __init__(self, session: CellSession, display: X11Display) -> None:
        """Build the antennae for `display`.

        Args:
            session: The Cell's session; every input runs through it.
            display: The X display to drive, with its authority and size.
        """
        self._session = session
        self._display = display

    async def move(self, point: Point) -> None:
        """Move the pointer to `point`; see Antennae."""
        self._check_on_screen(point, "move")
        await self._xdotool(("mousemove", "--sync", str(point.x), str(point.y)), "move")

    async def click(self, point: Point, button: MouseButton, count: int = 1) -> None:
        """Move to `point` and click `button` there `count` times; see Antennae."""
        self._check_on_screen(point, "click")
        if count not in (1, 2):
            raise PeripheralError(_PERIPHERAL, "click", f"count must be 1 or 2, got {count}")
        args: tuple[str, ...] = ("mousemove", "--sync", str(point.x), str(point.y), "click")
        args += ("--repeat", str(count), "--delay", str(_CLICK_GAP_MS), _BUTTONS[button])
        await self._xdotool(args, "click")

    async def type_text(self, text: str) -> None:
        """Type `text` into whatever has focus, paced per key; see Antennae."""
        timeout = INPUT_TIMEOUT_S + len(text) * _TYPE_SECONDS_PER_KEY
        # `--` ends xdotool's own options, so text beginning with "-" is typed, not parsed.
        args = ("type", "--delay", str(TYPE_DELAY_MS), "--", text)
        await self._xdotool(args, "type", timeout_s=timeout)

    async def press(self, keys: str) -> None:
        """Press one key chord; see Antennae."""
        await self._xdotool(("key", "--clearmodifiers", keys), "press")

    async def scroll(self, dx: int, dy: int, at: Point | None = None) -> None:
        """Scroll with wheel clicks, at `at` when given; see Antennae."""
        if at is not None:
            await self.move(at)
        # One click per wheel step on each axis that moves; a zero axis sends nothing.
        for steps, positive, negative in (
            (dy, _WHEEL_DOWN, _WHEEL_UP),
            (dx, _WHEEL_RIGHT, _WHEEL_LEFT),
        ):
            if steps:
                button = positive if steps > 0 else negative
                await self._xdotool(("click", "--repeat", str(abs(steps)), button), "scroll")

    async def pointer(self) -> Point:
        """Return where the pointer is now; see Antennae."""
        output = await self._xdotool(("getmouselocation", "--shell"), "read the pointer")
        found = dict(_LOCATION.findall(output.decode("utf-8", errors="replace")))
        if "X" not in found or "Y" not in found:
            raise PeripheralError(_PERIPHERAL, "read the pointer", "no X= and Y= in its output")
        return Point(int(found["X"]), int(found["Y"]))

    def _check_on_screen(self, point: Point, operation: str) -> None:
        """Refuse a point off this display before any command is built from it."""
        if not self._display.size.contains(point):
            size = self._display.size
            raise PeripheralError(
                _PERIPHERAL,
                operation,
                f"({point.x}, {point.y}) is off the {size.width}x{size.height} screen",
            )

    async def _xdotool(
        self, args: tuple[str, ...], operation: str, *, timeout_s: float = INPUT_TIMEOUT_S
    ) -> bytes:
        """Run one xdotool command against this display and return its stdout."""
        command = PeripheralCommand(
            argv=("xdotool", *args), timeout_s=timeout_s, env=self._display.environment()
        )
        return await run_peripheral(self._session, command, _PERIPHERAL, operation)
