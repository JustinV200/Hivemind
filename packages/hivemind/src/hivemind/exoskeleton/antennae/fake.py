"""Define FakeAntennae and InputEvent: pointer and keyboard input recorded in memory.

`FakeAntennae` drives no display: it checks every input exactly as the xdotool backend does (a
point off the screen and a click count other than 1 or 2 are refused the same way), moves its own
pointer, and appends an `InputEvent` for everything it did. An optional `on_input` callback lets a
fake scenario react the way an application would (a click on the login button paints the welcome
banner on a `FakeScreen`, a typed password fills a field). `fail_with` makes the next call fail the
way a vanished display does. Typed text is kept only in memory and never appears in an event's
`repr`, like every other rendering of typed text in the Hive.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.antennae`. Used by unit tests, the contract suite and fake Exoskeleton
    scenarios. Calls into `hivemind.exoskeleton.errors`, `.geometry` and waggle's MouseButton only.

Key invariants:
    - Every input the real backend refuses, this refuses with the same error type.
    - `repr(event)` never contains typed text.
    - Owns mutable state (codingrules 8.5): the pointer, the events and a pending failure, changed
      only through its own methods.

See Also:
    - hivemind.exoskeleton.antennae.xdotool for the real backend this imitates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Point, ScreenSize
from waggle.messages.capping import MouseButton

_PERIPHERAL = "antennae"  # How the fake names itself, as the real backend does.

__all__ = ["FakeAntennae", "InputEvent", "InputKind"]


class InputKind(Enum):
    """Which input an InputEvent records."""

    MOVE = "MOVE"
    CLICK = "CLICK"
    TYPE = "TYPE"
    PRESS = "PRESS"
    SCROLL = "SCROLL"


@dataclass(frozen=True, slots=True)
class InputEvent:
    """One input the fake received, with the fields its kind uses."""

    kind: InputKind
    point: Point | None = None  # Where a move, click or positioned scroll happened.
    button: MouseButton | None = None  # A click's button.
    count: int = 1  # A click's count: 2 for a double click.
    text: str | None = field(default=None, repr=False)  # What TYPE typed; never in a repr.
    keys: str | None = None  # What PRESS pressed.
    dx: int = 0  # A scroll's horizontal steps.
    dy: int = 0  # A scroll's vertical steps.


class FakeAntennae:
    """Record pointer and keyboard input against a screen size, and tell a scenario about it."""

    def __init__(
        self, size: ScreenSize, on_input: Callable[[InputEvent], None] | None = None
    ) -> None:
        """Build fake antennae for a screen of `size`, the pointer at its centre.

        Args:
            size: The screen the inputs land on; points off it are refused.
            on_input: Called with each accepted input, after it is recorded.
        """
        self._size = size
        self._on_input = on_input
        self._pointer = Point(size.width // 2, size.height // 2)
        self._events: list[InputEvent] = []
        self._failure: str | None = None

    @property
    def events(self) -> tuple[InputEvent, ...]:
        """Every accepted input, in order."""
        return tuple(self._events)

    def fail_with(self, reason: str) -> None:
        """Make the next call fail as a vanished display would."""
        self._failure = reason

    async def move(self, point: Point) -> None:
        """Move the pointer to `point`; see Antennae."""
        self._accept(InputEvent(InputKind.MOVE, point=point), "move")

    async def click(self, point: Point, button: MouseButton, count: int = 1) -> None:
        """Move to `point` and click there; see Antennae."""
        if count not in (1, 2):
            raise PeripheralError(_PERIPHERAL, "click", f"count must be 1 or 2, got {count}")
        event = InputEvent(InputKind.CLICK, point=point, button=button, count=count)
        self._accept(event, "click")

    async def type_text(self, text: str) -> None:
        """Type `text` into whatever has focus; see Antennae."""
        self._accept(InputEvent(InputKind.TYPE, text=text), "type")

    async def press(self, keys: str) -> None:
        """Press one key chord; see Antennae."""
        self._accept(InputEvent(InputKind.PRESS, keys=keys), "press")

    async def scroll(self, dx: int, dy: int, at: Point | None = None) -> None:
        """Scroll `dx`/`dy` wheel steps, at `at` when given; see Antennae."""
        self._accept(InputEvent(InputKind.SCROLL, point=at, dx=dx, dy=dy), "scroll")

    async def pointer(self) -> Point:
        """Return where the pointer is now; see Antennae."""
        self._raise_pending("read the pointer")
        return self._pointer

    def _accept(self, event: InputEvent, operation: str) -> None:
        """Check, record and announce one input, moving the pointer when it names a point."""
        if event.point is not None and not self._size.contains(event.point):
            size = self._size
            reason = (
                f"({event.point.x}, {event.point.y}) is off the {size.width}x{size.height} screen"
            )
            raise PeripheralError(_PERIPHERAL, operation, reason)
        self._raise_pending(operation)
        if event.point is not None:
            self._pointer = event.point
        self._events.append(event)
        if self._on_input is not None:
            self._on_input(event)

    def _raise_pending(self, operation: str) -> None:
        """Raise the failure `fail_with` queued, once."""
        if self._failure is not None:
            reason, self._failure = self._failure, None
            raise PeripheralError(_PERIPHERAL, operation, reason)
