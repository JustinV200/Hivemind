"""Run one typed GUI step on the attached peripherals: the one mapping from GuiOp to a call.

A GUI proposal's steps (waggle's `GuiStep`, ADR-0032) are applied by the Capping gate through the
Exoskeleton's surface, and this module is where each op meets its peripheral: pointer and keyboard
ops go to the Antennae, page ops to the Browser, SAY to the Buzz. A step whose peripheral was not
attached (a browser step on a desktop with no browser, a desktop click on a browser-only
attachment) fails as a PeripheralError naming what is missing, like any other failed step.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.surface`. Called by `surface.core.ExoskeletonSurface.apply`. Calls into
    `hivemind.exoskeleton.attach` (Peripherals), `.errors`, `.geometry` and waggle's GuiStep only.

Key invariants:
    - Every op in GuiOp has exactly one branch here; an unmapped op is a bug, not a no-op.
    - Typed text reaches only the peripheral that types it; it is never logged or returned.

See Also:
    - waggle.messages.capping.gui for the steps and which fields each op carries.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.exoskeleton.antennae.base import Antennae
from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.browser.base import Browser
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Point
from waggle.messages.capping import GuiOp, GuiStep, MouseButton

_DESKTOP = frozenset(
    {GuiOp.MOVE, GuiOp.CLICK, GuiOp.DOUBLE_CLICK, GuiOp.TYPE, GuiOp.PRESS, GuiOp.SCROLL}
)

__all__ = ["run_step"]


async def run_step(peripherals: Peripherals, step: GuiStep) -> None:
    """Apply `step` through the peripheral its op needs.

    Args:
        peripherals: What the attach provided.
        step: One validated step.

    Raises:
        PeripheralError: The peripheral is not attached, or it failed the step.
    """
    if step.op in _DESKTOP:
        await _desktop(_need(peripherals.antennae, "antennae", step), step)
    elif step.op is GuiOp.SAY:
        await _need(peripherals.buzz, "buzz", step).say(Path(step.clip or ""))
    else:
        await _browser(_need(peripherals.browser, "browser", step), step)


async def _desktop(antennae: Antennae, step: GuiStep) -> None:
    """Drive the pointer or keyboard for one desktop step."""
    point = Point(step.x, step.y) if step.x is not None and step.y is not None else None
    button = step.button or MouseButton.LEFT
    match step.op:
        case GuiOp.MOVE if point is not None:
            await antennae.move(point)
        case GuiOp.CLICK | GuiOp.DOUBLE_CLICK if point is not None:
            await antennae.click(point, button, 2 if step.op is GuiOp.DOUBLE_CLICK else 1)
        case GuiOp.TYPE:
            await antennae.type_text(step.text or "")
        case GuiOp.PRESS:
            await antennae.press(step.keys or "")
        case GuiOp.SCROLL:
            await antennae.scroll(step.dx or 0, step.dy or 0, at=point)
        case _:
            raise PeripheralError("antennae", step.op.value, "the step is missing its point")


async def _browser(browser: Browser, step: GuiStep) -> None:
    """Drive the page for one browser step."""
    match step.op:
        case GuiOp.NAVIGATE:
            await browser.navigate(step.url or "about:blank")
        case GuiOp.BROWSER_CLICK if step.target is not None:
            await browser.click(step.target)
        case GuiOp.BROWSER_FILL if step.target is not None:
            await browser.fill(step.target, step.text or "")
        case GuiOp.BROWSER_PRESS:
            await browser.press(step.keys or "", step.target)
        case _:
            raise PeripheralError("browser", step.op.value, "the step is missing its target")


def _need[PeripheralT](peripheral: PeripheralT | None, name: str, step: GuiStep) -> PeripheralT:
    """Return the peripheral a step needs, or fail the step naming what is not attached."""
    if peripheral is None:
        raise PeripheralError(name, step.op.value, f"no {name} is attached for this task")
    return peripheral
