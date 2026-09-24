"""Implement the Exoskeleton's desktop input tools: click, move, type, press and scroll.

The Antennae (a bee's organs of touch: the Exoskeleton's pointer and keyboard) drive the display
attached for this task. Each tool here turns one call into one typed waggle `GuiStep` and proposes
it through `act` (roadmap step 6.5, ADR-0032): the Capping gate applies it through the attached
Exoskeleton, verifies the call's `expect`, and rolls it back and raises an Alarm when that fails.
The tier comes from the display: `scratch_write` on one the lease started, `device_command` on the
operator's own. A point that leaves the screen is refused here when the display's size is known,
before anything is proposed, since a step that fails at apply time also raises an Alarm. Typed
text travels only inside its step; no result, reason or error here repeats it.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Offered by
    `offer.exoskeleton_specs` whenever the Antennae are attached. Calls into `hivemind.exoskeleton`
    (Point), `hivemind.llm`, this package's `act`, `arguments` and `errors`,
    `hivemind.workers.tools.registry` and waggle's GUI step models only.

Key invariants:
    - Every call proposes exactly one GUI step; nothing here touches the Antennae directly.
    - A result never contains typed text: it is the gate's outcome, rendered by
      `hivemind.workers.tools.proposals.tool_output`.

See Also:
    - hivemind.workers.tools.exoskeleton.act for the proposal every tool here makes.
    - hivemind.exoskeleton.antennae for the peripheral the gate drives.
    - waggle.messages.capping.gui for GuiStep and its bounds.
"""

from __future__ import annotations

from hivemind.exoskeleton import ExoskeletonHandle, Point
from hivemind.llm import JsonObject
from hivemind.workers.tools.exoskeleton.act import GuiAction, act, attached, desktop_reach, need
from hivemind.workers.tools.exoskeleton.arguments import (
    action_definition,
    build_step,
    flag,
    optional_int,
    required_int,
    required_text,
)
from hivemind.workers.tools.exoskeleton.errors import GuiArgumentError
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput, ToolSpec
from waggle.messages.capping import GuiOp, MouseButton

# One coordinate, as every desktop tool takes it: display pixels, (0, 0) at the top left.
_PIXEL: JsonObject = {"type": "integer", "description": "Screen pixels from the top-left corner."}
# One scroll axis: wheel steps, the unit the Antennae scroll by (waggle bounds it to +/-100).
_WHEEL: JsonObject = {"type": "integer", "description": "Wheel steps; positive scrolls right/down."}
# The buttons a model names, lowercase as people write them, mapped onto waggle's MouseButton.
_BUTTONS = {"left": MouseButton.LEFT, "middle": MouseButton.MIDDLE, "right": MouseButton.RIGHT}
# The sentence every desktop action's description ends with: who applies it, and what follows.
_GATED = "Proposed through the Capping gate, which applies it and then checks expect."

CLICK_DEFINITION = action_definition(
    "click",
    f"Click at (x, y) on this task's display. {_GATED}",
    {
        "x": _PIXEL,
        "y": _PIXEL,
        "button": {"type": "string", "enum": ["left", "middle", "right"]},
        "double": {"type": "boolean", "description": "True for a double click."},
    },
    ("x", "y"),
)
MOVE_DEFINITION = action_definition(
    "move",
    f"Move the pointer to (x, y) on this task's display. {_GATED}",
    {"x": _PIXEL, "y": _PIXEL},
    ("x", "y"),
)
TYPE_DEFINITION = action_definition(
    "type",
    f"Type text into whatever has keyboard focus on this task's display. {_GATED}",
    {
        "text": {"type": "string"},
        "secret": {"type": "boolean", "description": "True for a password or token: redacted."},
    },
    ("text",),
)
PRESS_DEFINITION = action_definition(
    "press",
    f"Press one key chord on this task's display, such as Return or ctrl+s. {_GATED}",
    {"keys": {"type": "string", "description": "Key names joined by +."}},
    ("keys",),
)
SCROLL_DEFINITION = action_definition(
    "scroll",
    f"Scroll by dx and dy wheel steps, at (x, y) when given. {_GATED}",
    {"dx": _WHEEL, "dy": _WHEEL, "x": _PIXEL, "y": _PIXEL},
    (),
)

__all__ = [
    "CLICK_DEFINITION",
    "CLICK_SPEC",
    "MOVE_DEFINITION",
    "MOVE_SPEC",
    "PRESS_DEFINITION",
    "PRESS_SPEC",
    "SCROLL_DEFINITION",
    "SCROLL_SPEC",
    "TYPE_DEFINITION",
    "TYPE_SPEC",
    "click",
    "move",
    "press",
    "scroll",
    "type_text",
]


async def click(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose one click (or double click) at (x, y); see `CLICK_DEFINITION`.

    Args:
        invocation: This attempt's context and assignment.
        arguments: `x`, `y`, optional `button` and `double`, `expect` and `irreversible`.

    Returns:
        The gate's outcome, as tool output.

    Raises:
        GuiArgumentError: A malformed argument, or a point off the screen.
        PeripheralMissingError: No pointer and keyboard are attached.
    """
    handle = _input(invocation)
    point = _point(handle, required_int(arguments, "x"), required_int(arguments, "y"))
    op = GuiOp.DOUBLE_CLICK if flag(arguments, "double") else GuiOp.CLICK
    button = _button(arguments)
    step = build_step({"op": op, "x": point.x, "y": point.y, "button": button})
    return await act(invocation, GuiAction("click", (step,), desktop_reach(handle), arguments))


async def move(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose moving the pointer to (x, y); see `MOVE_DEFINITION` and `click` for the rest."""
    handle = _input(invocation)
    point = _point(handle, required_int(arguments, "x"), required_int(arguments, "y"))
    step = build_step({"op": GuiOp.MOVE, "x": point.x, "y": point.y})
    return await act(invocation, GuiAction("move", (step,), desktop_reach(handle), arguments))


async def type_text(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose typing `text` where the keyboard focus is; see `TYPE_DEFINITION`.

    Named `type_text` so it never shadows the builtin; the model calls it `type`. `secret` marks
    the step so every recording and rendering of it redacts the text.
    """
    handle = _input(invocation)
    text = required_text(arguments, "text")
    step = build_step({"op": GuiOp.TYPE, "text": text, "secret": flag(arguments, "secret")})
    return await act(invocation, GuiAction("type", (step,), desktop_reach(handle), arguments))


async def press(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose pressing one key chord; GuiStep holds it to waggle's KEYS_PATTERN."""
    handle = _input(invocation)
    step = build_step({"op": GuiOp.PRESS, "keys": required_text(arguments, "keys")})
    return await act(invocation, GuiAction("press", (step,), desktop_reach(handle), arguments))


async def scroll(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Propose scrolling dx/dy wheel steps, at (x, y) when both are given; see `click`."""
    handle = _input(invocation)
    x, y = optional_int(arguments, "x"), optional_int(arguments, "y")
    fields: dict[str, object] = {"op": GuiOp.SCROLL}
    # A zero axis is left out rather than sent: GuiStep needs one non-zero axis, not two fields.
    for axis in ("dx", "dy"):
        steps = optional_int(arguments, axis)
        if steps:
            fields[axis] = steps
    if (x is None) != (y is None):
        raise GuiArgumentError("scroll takes both x and y, or neither.")
    if x is not None and y is not None:
        point = _point(handle, x, y)
        fields.update(x=point.x, y=point.y)
    step = build_step(fields)
    return await act(invocation, GuiAction("scroll", (step,), desktop_reach(handle), arguments))


CLICK_SPEC = ToolSpec(definition=CLICK_DEFINITION, run=click)
MOVE_SPEC = ToolSpec(definition=MOVE_DEFINITION, run=move)
TYPE_SPEC = ToolSpec(definition=TYPE_DEFINITION, run=type_text)
PRESS_SPEC = ToolSpec(definition=PRESS_DEFINITION, run=press)
SCROLL_SPEC = ToolSpec(definition=SCROLL_DEFINITION, run=scroll)


def _input(invocation: ToolInvocation) -> ExoskeletonHandle:
    """Return the attached handle once its pointer and keyboard are known to be there."""
    handle = attached(invocation)
    need(handle.peripherals.antennae, "pointer and keyboard")
    return handle


def _point(handle: ExoskeletonHandle, x: int, y: int) -> Point:
    """Build the point, refusing one outside the pixel range or off a screen of known size."""
    try:
        point = Point(x, y)
    except ValueError as error:
        raise GuiArgumentError(str(error)) from error
    eye = handle.peripherals.compound_eye
    if eye is not None and not eye.screen.contains(point):
        screen = eye.screen
        raise GuiArgumentError(f"({x}, {y}) is off the {screen.width}x{screen.height} screen.")
    return point


def _button(arguments: JsonObject) -> MouseButton | None:
    """Return the named button, or None (the left one) when the call named none."""
    name = arguments.get("button")
    if name is None:
        return None
    if not isinstance(name, str) or name not in _BUTTONS:
        raise GuiArgumentError(f"button must be one of {sorted(_BUTTONS)}.")
    return _BUTTONS[name]
