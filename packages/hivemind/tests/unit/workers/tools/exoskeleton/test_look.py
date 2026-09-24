"""Unit tests for hivemind.workers.tools.exoskeleton.look: see and the read-only page tools.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/exoskeleton/look.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.tools.exoskeleton.look for the module under test.
"""

from __future__ import annotations

import base64

from builders.llm import make_bound
from builders.workers import make_gui_context, run_tool

from hivemind.exoskeleton import PNG_MEDIA_TYPE, Peripherals, Region, ScreenSize
from hivemind.exoskeleton.browser.fake import (
    FIXTURE_ORIGIN,
    LOGIN_HEADING,
    LOGIN_TITLE,
    LONG_TEXT,
    FakeBrowser,
    login_site,
)
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.exoskeleton.frames import PNG_SIGNATURE
from hivemind.llm import FakeLLMProvider, ImagePart, ProviderCapabilities
from hivemind.pheromone import TrailQuery
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.exoskeleton.look import MAX_PAGE_READ_CHARS, _bounded
from hivemind.workers.tools.registry import build_registry
from waggle.clock import FakeClock

_SIZE = ScreenSize(200, 100)
_BLIND = ProviderCapabilities.full().model_copy(update={"vision": False})


async def _context(*, vision: bool = True, page: str = "/login") -> WorkerContext:
    """A display, and the fake browser already on `page` of the fixture site."""
    clock = FakeClock()
    screen = FakeScreen(_SIZE)
    screen.paint(Region(x=0, y=0, width=10, height=10), (200, 0, 0))
    browser = FakeBrowser(login_site(), clock)
    await browser.navigate(f"{FIXTURE_ORIGIN}{page}")
    capabilities = ProviderCapabilities.full() if vision else _BLIND
    return make_gui_context(
        Peripherals(compound_eye=FakeCompoundEye(screen, clock), browser=browser),
        clock,
        bound=make_bound(provider=FakeLLMProvider(capabilities=capabilities)),
    )


def _png(part: ImagePart) -> bytes:
    """Decode an ImagePart's bytes."""
    return base64.b64decode(part.data_base64)


async def test_see_returns_the_screen_as_an_image_and_only_its_size_as_text() -> None:
    ctx = await _context()

    result = await run_tool(ctx, "see", {})

    (image,) = result.media
    assert isinstance(image, ImagePart)
    assert image.media_type == PNG_MEDIA_TYPE
    assert _png(image).startswith(PNG_SIGNATURE)
    assert result.text == "Captured the whole screen as a 200x100 image, attached."
    assert image.data_base64 not in result.text


async def test_see_captures_only_the_region_asked_for() -> None:
    ctx = await _context()

    result = await run_tool(ctx, "see", {"region": "0,0,10,10"})

    assert "region 0,0,10,10" in result.text and "10x10 image" in result.text


async def test_see_refuses_a_region_off_the_screen() -> None:
    ctx = await _context()

    result = await run_tool(ctx, "see", {"region": "150,50,100,100"})

    assert result.media == ()
    assert "does not fit the 200x100 screen" in result.text


async def test_browser_screenshot_returns_the_viewport_as_an_image() -> None:
    ctx = await _context()

    result = await run_tool(ctx, "browser_screenshot", {})

    (image,) = result.media
    assert isinstance(image, ImagePart) and _png(image).startswith(PNG_SIGNATURE)
    assert result.text.startswith("Captured the browser's viewport as a ")


async def test_neither_image_tool_is_offered_to_a_model_without_vision() -> None:
    ctx = await _context(vision=False)

    names = {definition.name for definition in build_registry(ctx).definitions()}

    assert not names & {"see", "browser_screenshot"}
    assert {"browser_snapshot", "browser_read"} <= names  # The fast path still reads structure.


async def test_browser_snapshot_reads_the_url_title_and_accessibility_tree() -> None:
    ctx = await _context()

    text = (await run_tool(ctx, "browser_snapshot", {})).text

    assert text.startswith(f"url: {FIXTURE_ORIGIN}/login\ntitle: {LOGIN_TITLE}\n")
    assert f'button "{LOGIN_HEADING}"' in text


async def test_browser_read_returns_one_elements_text_or_says_nothing_matches() -> None:
    ctx = await _context()

    heading = await run_tool(ctx, "browser_read", {"target": {"role": "heading"}})
    missing = await run_tool(ctx, "browser_read", {"target": {"text": "Not on this page"}})

    assert heading.text == LOGIN_HEADING
    assert missing.text.startswith("no element matches text='Not on this page'")


async def test_browser_read_of_a_page_longer_than_any_read_stays_within_the_bound() -> None:
    ctx = await _context(page="/long")

    text = (await run_tool(ctx, "browser_read", {})).text

    assert len(LONG_TEXT) > MAX_PAGE_READ_CHARS
    assert 0 < len(text) <= MAX_PAGE_READ_CHARS


def test_bounded_cuts_a_long_read_with_a_marker_naming_its_true_length() -> None:
    text = "x" * (MAX_PAGE_READ_CHARS + 5)

    cut = _bounded(text)

    assert cut.startswith("x" * MAX_PAGE_READ_CHARS)
    assert cut.endswith(f"[truncated, {MAX_PAGE_READ_CHARS + 5} chars total]")
    assert _bounded("short") == "short"


async def test_read_only_tools_never_propose_anything() -> None:
    ctx = await _context()

    for name in ("see", "browser_screenshot", "browser_snapshot", "browser_read"):
        await run_tool(ctx, name, {})

    kinds = [event.kind for event in await ctx.trail.query(TrailQuery())]
    assert not [kind for kind in kinds if kind.startswith("capping.")]


async def test_a_display_that_vanished_is_a_readable_failure() -> None:
    clock = FakeClock()
    screen = FakeScreen(_SIZE)
    ctx = make_gui_context(Peripherals(compound_eye=FakeCompoundEye(screen, clock)), clock)
    screen.vanish()

    result = await run_tool(ctx, "see", {})

    assert result.text == "see failed: the display is gone"
    assert result.media == ()
