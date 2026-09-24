"""Drive a lease's Chromium page with Playwright: PlaywrightBrowser, the fast path's real backend.

The browser fast path (roadmap step 6.11, ADR-0031) drives a Chromium the lease started on the
Cell through Playwright attached over CDP (`.connect`). `PlaywrightBrowser` implements `Browser` on
the one page it was given. Elements are named the way a person would name them (role and
accessible name, label, visible text; a CSS selector last), only visible ones count, and exactly
one must match (`browser.targets`). Reads come back as structure and bounded (`browser.excerpts`):
Playwright's aria snapshot of the page, with every password field's value blanked out because
Playwright prints field values verbatim; the page's visible text; one element's text, and never a
password's. A navigation waits for the document to commit and then to load, so a page that
redirects itself on load (a login guard) is followed rather than reported as interrupted; a failed
navigation waits for the error page Chromium commits a moment after the failure is reported, so it
cannot overlap (and drop) the next navigation, and a navigation an error page still displaced is
asked for once more. Checkpoint and restore are `.storage`'s; restore then reloads the checkpoint's
URL. Every file request goes through `.guard.FileGuard`, installed before the first page loads, and
a navigation to a file URL outside the lease's file roots is refused before it starts.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Built by `connect_browser`, which
    `browser.launch.ChromiumLauncher` calls; driven by attach's handle, the Capping gate's GUI
    surface and the `browser_*` tools. Calls into Playwright, `.calls`, `.connect`, `.guard`,
    `.storage`, `browser.targets`, `.keys`, `.excerpts`, `.files`, `.state`, `.base`,
    `hivemind.exoskeleton.frames`.

Key invariants:
    - Every Playwright call goes through `PlaywrightCalls.run`: bounded, mapped, refused after
      `close`.
    - `close` only disconnects: the browser process belongs to the lease, and detach stops it.
    - No file outside the lease's file roots is ever loaded, by a navigation or by the page.
    - Neither `snapshot` nor `element_text` ever returns a password field's value.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for Playwright over CDP.
    - hivemind.exoskeleton.browser.base for the Browser contract each method implements.
    - hivemind.exoskeleton.browser.fake for the fake the same contract suite runs.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame as PageFrame
from playwright.async_api import Locator, Page

from hivemind.exoskeleton.browser.base import BrowserCheckpoint
from hivemind.exoskeleton.browser.excerpts import (
    MAX_ELEMENT_TEXT_CHARS,
    MAX_PAGE_TEXT_CHARS,
    MAX_SNAPSHOT_CHARS,
    REDACTED,
    bounded,
)
from hivemind.exoskeleton.browser.files import refuse_outside_roots
from hivemind.exoskeleton.browser.keys import browser_chord
from hivemind.exoskeleton.browser.playwright.calls import PlaywrightCalls, close_quietly
from hivemind.exoskeleton.browser.playwright.connect import (
    CdpConnection,
    PlaywrightTimeouts,
    attach_cdp,
)
from hivemind.exoskeleton.browser.playwright.guard import FileGuard
from hivemind.exoskeleton.browser.playwright.storage import CheckpointStorage
from hivemind.exoskeleton.browser.state import BrowserState
from hivemind.exoskeleton.browser.targets import PERIPHERAL, ambiguity, normalised
from hivemind.exoskeleton.errors import AttachError, PeripheralError
from hivemind.exoskeleton.frames import Frame
from waggle.clock import Clock
from waggle.messages.capping import ElementTarget

# How Playwright words a commit another navigation overlapped (a page redirecting itself, say).
_INTERRUPTED = "interrupted by another navigation"
# Where Chromium shows a failed load. Playwright reports the failure before Chromium commits this
# page, and a commit that late overlaps the next navigation and can drop it: about one run in ten
# of the contract suite's missing-page clause read the title of a page still loading (2026-09-24).
# So a failed navigation first waits for its error page to land, within ERROR_PAGE_WAIT_S.
_ERROR_PAGE = "chrome-error://"
ERROR_PAGE_WAIT_S = 2.0  # Chromium commits it within tens of milliseconds; this is a bound.
# A secret this long is cut out of a snapshot wherever it appears; a shorter one could match
# ordinary words, so it is cut only where a field's value is printed.
MIN_ANYWHERE_CHARS = 4
_PAGE_TEXT_JS = "() => document.body ? document.body.innerText : ''"
# A form field reads as its value, a password field as nothing, anything else as its visible text.
_ELEMENT_TEXT_JS = """element => {
  if (element instanceof HTMLInputElement) return element.type === 'password' ? '' : element.value;
  if (element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
    return element.value;
  }
  return element.innerText;
}"""
_PASSWORDS_JS = """() => Array.from(
  document.querySelectorAll('input[type=password]'), field => field.value).filter(Boolean)"""

__all__ = ["MIN_ANYWHERE_CHARS", "PlaywrightBrowser", "connect_browser", "redact_field_values"]


async def connect_browser(
    endpoint: str,
    clock: Clock,
    timeouts: PlaywrightTimeouts,
    file_roots: tuple[Path, ...] = (),
) -> PlaywrightBrowser:
    """Attach to the Chromium serving DevTools at `endpoint` and return a Browser on its page.

    Args:
        endpoint: "http://127.0.0.1:<port>", the port the browser wrote to DevToolsActivePort.
        clock: Stamps every screenshot.
        timeouts: How long attaching, loading and waiting for elements may take.
        file_roots: The only directories a file URL may load from (`BrowserLaunch.file_roots`);
            empty refuses every file URL.

    Returns:
        The connected browser, on the page Chromium opened (about:blank), with every file request
        routed through a `FileGuard` over `file_roots`.

    Raises:
        AttachError: Playwright's driver would not start, the browser could not be attached, or
            the file guard could not be installed (the connection is closed first).
    """
    connection = await attach_cdp(endpoint, timeouts)
    try:
        # WHY: installed before the bee's first navigation, so no page ever loads unguarded.
        await FileGuard(file_roots).install(connection.context)
    except (PlaywrightError, TimeoutError) as error:
        await close_quietly(connection.browser)
        await close_quietly(connection.driver)
        raise AttachError(f"could not guard the browser's file access: {error}") from error
    return PlaywrightBrowser(connection, clock, timeouts, file_roots)


class PlaywrightBrowser:
    """Drive one Chromium page over CDP; see Browser for each operation's contract."""

    def __init__(
        self,
        connection: CdpConnection,
        clock: Clock,
        timeouts: PlaywrightTimeouts,
        file_roots: tuple[Path, ...] = (),
    ) -> None:
        """Wrap an attached page.

        Args:
            connection: Playwright's handles on the browser, from `attach_cdp`.
            clock: Stamps every screenshot.
            timeouts: How long each kind of call may take.
            file_roots: The directories a navigation may open a file URL in; `connect_browser`
                also routes every other file request through the same roots.
        """
        self._connection = connection
        self._file_roots = file_roots
        self._page = connection.page
        self._clock = clock
        self._navigation_ms = timeouts.navigation_s * 1e3
        self._element_ms = timeouts.element_s * 1e3
        self._calls = PlaywrightCalls(timeouts.limit_s)
        self._storage = CheckpointStorage(
            self._page, connection.context, self._calls, self._navigation_ms
        )

    async def navigate(self, url: str) -> None:
        """Load `url` and wait for it (and any redirect it starts) to load; see Browser."""
        self._calls.check_open("navigate")
        # The route would block it too, but only after Chromium committed an error page; refusing
        # first gives the bee the reason and leaves the page it was on.
        refuse_outside_roots(url, self._file_roots, "navigate")
        await self._calls.run("navigate", lambda: self._load(url))

    async def click(self, target: ElementTarget) -> None:
        """Click the one visible element `target` names; see Browser."""
        locator = await self._resolve(target, "click")
        await self._calls.run("click", lambda: locator.click(timeout=self._element_ms))

    async def fill(self, target: ElementTarget, text: str) -> None:
        """Replace the value of the field `target` names with `text`; see Browser."""
        locator = await self._resolve(target, "fill")
        fill = locator.fill
        await self._calls.run("fill", lambda: fill(text, timeout=self._element_ms), typed=text)

    async def press(self, keys: str, target: ElementTarget | None = None) -> None:
        """Press one chord, on `target` when given, else wherever focus is; see Browser."""
        self._calls.check_open("press")
        try:
            chord = browser_chord(keys)
        except ValueError as error:
            raise PeripheralError(PERIPHERAL, "press", str(error)) from error
        if target is None:
            await self._calls.run("press", lambda: self._page.keyboard.press(chord))
            return
        locator = await self._resolve(target, "press")
        await self._calls.run("press", lambda: locator.press(chord, timeout=self._element_ms))

    async def url(self) -> str:
        """Return the page's URL; see Browser."""
        self._calls.check_open("read the URL")
        return self._page.url

    async def title(self) -> str:
        """Return the page's title; see Browser."""
        return await self._calls.run("read the title", self._page.title)

    async def snapshot(self) -> str:
        """Return the page's aria snapshot, password values blanked, bounded; see Browser."""
        root = self._page.locator(":root")
        tree = await self._calls.run(
            "snapshot", lambda: root.aria_snapshot(timeout=self._element_ms)
        )
        passwords: list[str] = []
        # Every attached frame's password fields: a snapshot shows an iframe's fields too.
        for frame in self._page.frames:
            if not frame.is_detached():
                passwords.extend(await _passwords(self._calls, frame))
        return bounded(redact_field_values(tree, passwords), MAX_SNAPSHOT_CHARS)

    async def element_text(self, target: ElementTarget) -> str | None:
        """Return the one visible element's text, or None at once when none; see Browser."""
        operation = "read an element"
        locator = _locator(self._page, target)
        count = await self._calls.run(operation, locator.count)
        if count == 0:
            return None  # Not there now: this read never waits (Browser.element_text).
        if count > 1:
            raise ambiguity(target, count, operation)
        evaluate = locator.evaluate
        value = await self._calls.run(
            operation, lambda: evaluate(_ELEMENT_TEXT_JS, timeout=self._element_ms)
        )
        return bounded(_text(value, operation), MAX_ELEMENT_TEXT_CHARS)

    async def page_text(self) -> str:
        """Return the page's visible text, bounded; see Browser."""
        value = await self._calls.run("read the page", lambda: self._page.evaluate(_PAGE_TEXT_JS))
        return bounded(_text(value, "read the page"), MAX_PAGE_TEXT_CHARS)

    async def screenshot(self) -> Frame:
        """Capture the page's viewport as a PNG Frame; see Browser."""
        shoot = self._page.screenshot
        png = await self._calls.run(
            "screenshot", lambda: shoot(type="png", timeout=self._navigation_ms)
        )
        try:
            return Frame.from_png(png, self._clock.now())
        except ValueError as error:
            raise PeripheralError(PERIPHERAL, "screenshot", str(error)) from error

    async def checkpoint(self) -> BrowserCheckpoint:
        """Take the URL, every cookie and every loaded origin's local storage; see Browser."""
        url = await self.url()
        state = await self._storage.capture()
        return BrowserCheckpoint(url=url, state=state.dump())

    async def restore(self, checkpoint: BrowserCheckpoint) -> None:
        """Put back exactly the checkpoint's cookies and storage, then load its URL; see Browser."""
        self._calls.check_open("restore")
        try:
            state = BrowserState.load(checkpoint.state)
        except ValueError as error:
            reason = "the checkpoint was not taken by a browser like this one"
            raise PeripheralError(PERIPHERAL, "restore", reason) from error
        await self._storage.replace(state)
        await self._calls.run("restore", lambda: self._load(checkpoint.url))

    async def close(self) -> None:
        """Disconnect and stop Playwright's driver, leaving the browser running; idempotent."""
        if not self._calls.close():
            return
        # Over CDP closing ends the connection only: the process is the lease's to stop. Both are
        # best effort, since a browser the lease stopped first has nothing left to disconnect.
        await close_quietly(self._connection.browser)
        await close_quietly(self._connection.driver)

    async def _resolve(self, target: ElementTarget, operation: str) -> Locator:
        """Wait for the visible element `target` names and insist it is the only one."""
        locator = _locator(self._page, target)
        wait = locator.first.wait_for
        await self._calls.run(
            operation, lambda: wait(state="visible", timeout=self._element_ms), missing=target
        )
        count = await self._calls.run(operation, locator.count)
        if count > 1:
            raise ambiguity(target, count, operation)
        return locator

    async def _load(self, url: str) -> None:
        """Commit `url`, then wait for the page to load wherever that navigation settles."""
        if not await self._commit(url) and self._page.url.startswith(_ERROR_PAGE):
            # Overlapped, and what overlapped it was a failed load's error page landing in our
            # place: nothing is in flight now, so asking again collides with nothing.
            await self._commit(url)
        await self._page.wait_for_load_state("load", timeout=self._navigation_ms)

    async def _commit(self, url: str) -> bool:
        """Start loading `url` and wait for it to commit; False when another navigation overlapped.

        A failure is settled before it is raised: Chromium commits the failed load's error page a
        moment after Playwright reports the failure, and that commit must not overlap whatever
        the bee does next.

        Raises:
            PlaywrightError: The navigation failed for any other reason (a network error, a
                missing file, a timeout): that failure is real.
        """
        landed: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def on_navigated(frame: PageFrame) -> None:
            # Only the main frame's error page counts; the first one settles the failure.
            main = frame == self._page.main_frame
            if main and frame.url.startswith(_ERROR_PAGE) and not landed.done():
                landed.set_result(None)

        self._page.on("framenavigated", on_navigated)
        try:
            await self._page.goto(url, wait_until="commit", timeout=self._navigation_ms)
        except PlaywrightError as error:
            if _INTERRUPTED in error.message:
                # Overlapped, not cancelled: let the navigation that overlapped ours land.
                await self._page.wait_for_load_state("load", timeout=self._navigation_ms)
                return False
            await _settle(self._page, landed, self._navigation_ms)
            raise
        finally:
            self._page.remove_listener("framenavigated", on_navigated)
        return True


def redact_field_values(snapshot: str, values: Iterable[str]) -> str:
    """Blank out every listed value where an aria snapshot prints a field's value, and anywhere.

    Playwright renders a field as `- textbox "Password": value`, the value's whitespace collapsed
    and trimmed, then double-quoted and escaped when YAML needs it. Every one of those spellings
    is replaced at the end of a line, exactly; then any value of MIN_ANYWHERE_CHARS or more still
    found anywhere is cut out too, so a rendering this did not foresee still cannot show it. A
    shorter value is replaced only where a field's value goes, so it never garbles the rest.

    Args:
        snapshot: An aria snapshot.
        values: The values to hide: every password field's.

    Returns:
        The snapshot with each such value replaced by REDACTED.

    Example:
        >>> redact_field_values('- textbox "Password": honeycomb', ["honeycomb"])
        '- textbox "Password": [redacted]'
    """
    plain = {form for value in values for form in (value, normalised(value)) if form}
    endings = {f": {spelling}" for form in plain for spelling in (form, _yaml_quoted(form))}
    lines = snapshot.split("\n")
    # Each line ends in at most one field value; the first listed ending that matches is it.
    for index, line in enumerate(lines):
        ending = next((ending for ending in endings if line.endswith(ending)), None)
        if ending is not None:
            lines[index] = f"{line[: -len(ending)]}: {REDACTED}"
    redacted = "\n".join(lines)
    # Longest first, so a value that contains another is cut whole.
    for form in sorted(plain, key=len, reverse=True):
        if len(form) >= MIN_ANYWHERE_CHARS:
            redacted = redacted.replace(form, REDACTED)
    return redacted


async def _settle(page: Page, landed: asyncio.Future[None], navigation_ms: float) -> None:
    """Wait, briefly, for a failed load's error page to commit and load; never raise."""
    # A failure without an error page (a timeout, a closed page) simply runs out the bound.
    with contextlib.suppress(TimeoutError, PlaywrightError):
        async with asyncio.timeout(ERROR_PAGE_WAIT_S):
            await landed
            await page.wait_for_load_state("load", timeout=navigation_ms)


async def _passwords(calls: PlaywrightCalls, frame: PageFrame) -> list[str]:
    """Return the non-empty values of one frame's password fields."""
    values = await calls.run("snapshot", lambda: frame.evaluate(_PASSWORDS_JS))
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str)]


def _locator(page: Page, target: ElementTarget) -> Locator:
    """Map `target` onto a locator over the page's visible elements only."""
    match target:
        case ElementTarget(role=str() as role):
            # Playwright types `role` as a closed set of ARIA roles; an unknown one matches nothing.
            found = page.get_by_role(cast(Any, role), name=target.name, exact=True)
        case ElementTarget(label=str() as label):
            found = page.get_by_label(label, exact=True)
        case ElementTarget(text=str() as text):
            found = page.get_by_text(text, exact=True)
        case ElementTarget(selector=str() as selector):
            found = page.locator(selector)
        case _:
            # ElementTarget's own validator requires one of the four, so this never runs.
            raise PeripheralError(PERIPHERAL, "find an element", f"{target.describe()} names none")
    return found.filter(visible=True)


def _text(value: object, operation: str) -> str:
    """Return a script's result as text, or fail the operation when it is not text."""
    if not isinstance(value, str):
        raise PeripheralError(PERIPHERAL, operation, "the page returned something other than text")
    return value


def _yaml_quoted(value: str) -> str:
    """Spell `value` the way an aria snapshot quotes one: in double quotes, backslash-escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
