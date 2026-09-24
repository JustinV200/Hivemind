"""Define Antennae: how a Worker drives its Cell's pointer and keyboard, through the Cell's session.

The Antennae (a bee's organs of touch) are the Exoskeleton's input peripheral: move the pointer,
click, type text, press a key chord, scroll. Every call is one short command run on the Cell
through its `CellSession` (codingrules section 8.7), so the same backend drives a Virtual Cell's
display and a Linux Real Cell's. The Antennae never decide whether an input may happen: every call
here is made by the Capping gate's GUI surface applying a proposal that was already checked, or by
attach proving the display takes input at all.

Latency classes used below: *interactive* means tens to a few hundred milliseconds (one short
command on the Cell); *proportional* means it grows with the input (typing is paced per key).
Every call is bounded by the implementation's own input timeout.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`.
    Implemented by `hivemind.exoskeleton.antennae.xdotool.XdotoolAntennae` and
    `hivemind.exoskeleton.antennae.fake.FakeAntennae`; called by the Capping gate's GUI surface
    (`hivemind.exoskeleton.surface`). Calls into `hivemind.exoskeleton.geometry` and waggle's
    MouseButton only.

Key invariants:
    - Key chords are validated by waggle's KEYS_PATTERN before they reach an implementation, so
      no implementation ever receives shell or injection metacharacters as a key name.
    - Typed text is passed as one argument, never through a shell, and is never logged.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the xdotool backend.
    - waggle.messages.capping.gui for GuiStep, the typed input a GUI proposal carries.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.exoskeleton.geometry import Point
from waggle.messages.capping import MouseButton

__all__ = ["Antennae"]


class Antennae(Protocol):
    """Drive the Cell's pointer and keyboard: move, click, type, press, scroll, and report."""

    async def move(self, point: Point) -> None:
        """Move the pointer to `point`.

        Latency: interactive. Failure: PeripheralError when the display is gone or the command
        fails or times out.

        Args:
            point: Where to move, in display pixels.

        Raises:
            PeripheralError: The move failed.
        """
        ...

    async def click(self, point: Point, button: MouseButton, count: int = 1) -> None:
        """Move to `point` and click `button` there `count` times (2 for a double click).

        Latency: interactive. Failure: PeripheralError, as `move`.

        Args:
            point: Where to click, in display pixels.
            button: Which button.
            count: How many clicks, 1 or 2.

        Raises:
            PeripheralError: The click failed.
        """
        ...

    async def type_text(self, text: str) -> None:
        """Type `text` into whatever has keyboard focus.

        Latency: proportional to the text's length (keys are paced so applications keep up).
        Failure: PeripheralError, as `move`.

        Args:
            text: What to type; newlines press Return.

        Raises:
            PeripheralError: Typing failed.
        """
        ...

    async def press(self, keys: str) -> None:
        """Press one key chord ("ctrl+s", "Return") and release it.

        Latency: interactive. Failure: PeripheralError, as `move`.

        Args:
            keys: The chord, in waggle's KEYS_PATTERN syntax.

        Raises:
            PeripheralError: The key press failed.
        """
        ...

    async def scroll(self, dx: int, dy: int, at: Point | None = None) -> None:
        """Scroll `dx` columns and `dy` rows of wheel steps, at `at` when given.

        Latency: interactive. Failure: PeripheralError, as `move`.

        Args:
            dx: Horizontal wheel steps; positive scrolls right.
            dy: Vertical wheel steps; positive scrolls down.
            at: Where to move the pointer first; None scrolls wherever it is.

        Raises:
            PeripheralError: The scroll failed.
        """
        ...

    async def pointer(self) -> Point:
        """Return where the pointer is now.

        Latency: interactive. Failure: PeripheralError, as `move`.

        Returns:
            The pointer's position in display pixels.

        Raises:
            PeripheralError: The position could not be read.
        """
        ...
