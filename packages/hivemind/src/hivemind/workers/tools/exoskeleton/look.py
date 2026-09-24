"""Implement the Exoskeleton's read-only sight tools: see, and read the page as image or text.

Looking changes nothing, so none of these tools proposes anything to the Capping gate (the quality
gate every side effect passes, codingrules 8.12): each reads its peripheral and returns what it
found. `see` captures the display (the CompoundEye, the Exoskeleton's vision) and
`browser_screenshot` the browser's viewport, each as an image the model itself looks at: the frame
travels only as a `hivemind.llm.ImagePart` in the result's media, never in its text or a log
(codingrules section 12), and both are offered only to a model that declares `vision`.
`browser_snapshot` (the page's accessibility tree, with its URL and title) and `browser_read` (one
element's text, or the page's visible text) are the fast path's structural reads, which let a
model without vision find its way around a page (roadmap step 6.11); both are cut to a bound, with
a marker, so a huge page never floods a turn.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools.exoskeleton`. Offered by
    `offer.exoskeleton_specs`. Calls into `hivemind.exoskeleton` (Frame, PNG_MEDIA_TYPE,
    PeripheralError), `hivemind.llm` (ImagePart), this package's `act`, `arguments` and `errors`,
    and `hivemind.workers.tools.registry` only.

Key invariants:
    - Nothing here proposes, and nothing here drives a peripheral: reads only.
    - A frame's bytes appear only in a result's media; its text says the size and nothing more.

See Also:
    - hivemind.exoskeleton.compound_eye and hivemind.exoskeleton.browser for the reads made.
    - hivemind.llm.providers for how an adapter sends an image, or refuses one without vision.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable

from hivemind.exoskeleton import PNG_MEDIA_TYPE, Browser, Frame, PeripheralError
from hivemind.llm import ImagePart, JsonObject
from hivemind.workers.tools.exoskeleton.act import attached, need
from hivemind.workers.tools.exoskeleton.arguments import (
    TARGET_SCHEMA,
    read_definition,
    region_from,
    target_from,
)
from hivemind.workers.tools.exoskeleton.errors import PeripheralReadError
from hivemind.workers.tools.registry import ToolInvocation, ToolOutput, ToolSpec

# About five thousand tokens: a page's tree or text a model can read in one turn. Mirrors the
# browser's own read cap, so a browser that cut its read already never gets cut twice.
MAX_PAGE_READ_CHARS = 20_000
_READ_ONLY = "Read-only: nothing is proposed or changed."  # How every description here ends.

SEE_DEFINITION = read_definition(
    "see",
    f"Capture this task's display, or one region of it, and look at the image. {_READ_ONLY}",
    {"region": {"type": "string", "description": "'x,y,width,height'; omit for the screen."}},
)
BROWSER_SCREENSHOT_DEFINITION = read_definition(
    "browser_screenshot", f"Capture the browser's viewport and look at the image. {_READ_ONLY}", {}
)
BROWSER_SNAPSHOT_DEFINITION = read_definition(
    "browser_snapshot",
    "Read the page's accessibility tree (roles, names, values) with its URL and title: how to "
    f"find the target for a browser action without seeing the page. {_READ_ONLY}",
    {},
)
BROWSER_READ_DEFINITION = read_definition(
    "browser_read",
    f"Read the text of one element (target), or the page's visible text. {_READ_ONLY}",
    {"target": TARGET_SCHEMA},
)

__all__ = [
    "BROWSER_READ_DEFINITION",
    "BROWSER_READ_SPEC",
    "BROWSER_SCREENSHOT_DEFINITION",
    "BROWSER_SCREENSHOT_SPEC",
    "BROWSER_SNAPSHOT_DEFINITION",
    "BROWSER_SNAPSHOT_SPEC",
    "MAX_PAGE_READ_CHARS",
    "SEE_DEFINITION",
    "SEE_SPEC",
    "browser_read",
    "browser_screenshot",
    "browser_snapshot",
    "see",
]


async def see(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Capture the display, or `region` of it, as an image for the model.

    Args:
        invocation: This attempt's context and assignment.
        arguments: An optional `region`, "x,y,width,height" on the screen.

    Returns:
        The frame's size as text and the frame itself as media.

    Raises:
        GuiArgumentError: `region` is malformed or leaves the screen.
        PeripheralMissingError: No display is attached.
        PeripheralReadError: The capture failed.
    """
    eye = need(attached(invocation).peripherals.compound_eye, "display")
    raw = arguments.get("region")
    region = None if raw is None else region_from(raw, eye.screen, "region")
    frame = await _read("see", eye.capture(region))
    what = "the whole screen" if region is None else f"region {region.spec()}"
    return _image(frame, f"Captured {what}")


async def browser_screenshot(invocation: ToolInvocation, arguments: JsonObject) -> ToolOutput:
    """Capture the browser's viewport as an image for the model; see `see` for the shape."""
    browser = _browser(invocation)
    frame = await _read("browser_screenshot", browser.screenshot())
    return _image(frame, "Captured the browser's viewport")


async def browser_snapshot(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Return the page's URL, title and accessibility tree, the tree cut to the read bound.

    Raises:
        PeripheralMissingError: No browser is attached.
        PeripheralReadError: A read failed.
    """
    browser = _browser(invocation)
    url = await _read("browser_snapshot", browser.url())
    title = await _read("browser_snapshot", browser.title())
    tree = await _read("browser_snapshot", browser.snapshot())
    return f"url: {url}\ntitle: {title}\n\n{_bounded(tree)}"


async def browser_read(invocation: ToolInvocation, arguments: JsonObject) -> str:
    """Return one element's text when `target` names one, else the page's visible text, bounded.

    Raises:
        GuiArgumentError: `target` does not name one element one way.
        PeripheralMissingError: No browser is attached.
        PeripheralReadError: The read failed.
    """
    browser = _browser(invocation)
    raw = arguments.get("target")
    if raw is None:
        return _bounded(await _read("browser_read", browser.page_text()))
    target = target_from(raw)
    text = await _read("browser_read", browser.element_text(target))
    if text is None:
        return f"no element matches {target.describe()} on this page."
    return _bounded(text)


SEE_SPEC = ToolSpec(definition=SEE_DEFINITION, run=see)
BROWSER_SCREENSHOT_SPEC = ToolSpec(definition=BROWSER_SCREENSHOT_DEFINITION, run=browser_screenshot)
BROWSER_SNAPSHOT_SPEC = ToolSpec(definition=BROWSER_SNAPSHOT_DEFINITION, run=browser_snapshot)
BROWSER_READ_SPEC = ToolSpec(definition=BROWSER_READ_DEFINITION, run=browser_read)


def _browser(invocation: ToolInvocation) -> Browser:
    """Return this task's attached browser, or refuse the call when it has none."""
    return need(attached(invocation).peripherals.browser, "browser")


async def _read[ResultT](tool: str, pending: Awaitable[ResultT]) -> ResultT:
    """Await one peripheral read, turning its failure into a message the model can act on."""
    try:
        # External await: one capture or one round trip to the browser, bounded by the
        # peripheral's own timeout; a timeout arrives here as a PeripheralError like any failure.
        return await pending
    except PeripheralError as error:
        raise PeripheralReadError(tool, error.reason) from error


def _image(frame: Frame, what: str) -> ToolOutput:
    """Hand `frame` to the model as media, and say only its size in the text."""
    data = base64.b64encode(frame.png).decode("ascii")
    return ToolOutput(
        text=f"{what} as a {frame.width}x{frame.height} image, attached.",
        media=(ImagePart(media_type=PNG_MEDIA_TYPE, data_base64=data),),
    )


def _bounded(text: str) -> str:
    """Cut `text` to MAX_PAGE_READ_CHARS, with a marker naming the true length when cut."""
    if len(text) <= MAX_PAGE_READ_CHARS:
        return text
    return f"{text[:MAX_PAGE_READ_CHARS]}...[truncated, {len(text)} chars total]"
