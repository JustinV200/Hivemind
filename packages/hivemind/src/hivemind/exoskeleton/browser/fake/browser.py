"""Provide FakeBrowser: a Browser over a FakeSite, in memory, as strict as the real one.

The browser contract suite runs every clause over the real Playwright browser and over this fake,
so the fake must fail the way the real one does, not merely succeed the way it does. It serves a
`FakeSite` (`.site`): navigating loads a page (following its guard), and clicking or pressing
Enter runs the element's actions. Targets resolve only among visible elements and must name
exactly one (`browser.targets`), exact names compared as Playwright compares them. Chords go
through the same translation (`browser.keys`); Enter runs the focused or named element's Enter
actions and every other chord is a keystroke with nothing bound to it. Reads are bounded by the
same caps (`browser.excerpts`), a password field's value never reads back, and a screenshot is a
real PNG, a solid colour that differs page by page. A checkpoint is the same `BrowserState` the
real browser writes: every cookie and every origin's local storage, which restore puts back
whole. Shipped code (codingrules 14.4): demos and `hive doctor` use it too.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.fake`. Built by `.launcher.FakeBrowserLauncher` and by tests.
    Calls into `.site`, `browser.targets`, `.keys`, `.excerpts`, `.state`, `.base`,
    `hivemind.exoskeleton.errors`, `.frames` and `.geometry`.

Key invariants:
    - Every operation after `close` raises PeripheralError; `close` itself is idempotent.
    - A target is resolved before anything happens, so an operation on a missing or ambiguous
      target changes nothing.

See Also:
    - hivemind.exoskeleton.browser.base for the Browser contract.
    - hivemind.exoskeleton.browser.playwright for the real browser this mirrors.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from hivemind.exoskeleton.browser.base import BrowserCheckpoint
from hivemind.exoskeleton.browser.excerpts import (
    MAX_ELEMENT_TEXT_CHARS,
    MAX_PAGE_TEXT_CHARS,
    MAX_SNAPSHOT_CHARS,
    REDACTED,
    URL_MASK,
    bounded,
)
from hivemind.exoskeleton.browser.fake.site import (
    BLANK_PAGE,
    FakeAction,
    FakeElement,
    FakePage,
    FakeSite,
    Navigate,
    Reveal,
    SetCookie,
    SetStorage,
    Submit,
)
from hivemind.exoskeleton.browser.files import refuse_outside_roots
from hivemind.exoskeleton.browser.keys import ENTER, browser_chord
from hivemind.exoskeleton.browser.state import BrowserState, StoredCookie, storage_origin
from hivemind.exoskeleton.browser.targets import PERIPHERAL, ambiguity, normalised
from hivemind.exoskeleton.errors import ElementNotFoundError, PeripheralError
from hivemind.exoskeleton.frames import Frame, solid_png
from hivemind.exoskeleton.geometry import ScreenSize
from waggle.clock import Clock
from waggle.messages.capping import ElementTarget

FAKE_VIEWPORT = ScreenSize(320, 200)  # Small: a fake frame only has to be a real, decodable PNG.
MAX_GUARD_HOPS = 5  # Guards sending a visitor on more often than this are a broken fixture.

__all__ = ["FAKE_VIEWPORT", "MAX_GUARD_HOPS", "FakeBrowser"]


class FakeBrowser:
    """A Browser over a FakeSite; see Browser for each operation's contract.

    Owns mutable state (codingrules 8.5): the current page, its fields' values, the elements
    revealed on it, which element has focus, every cookie and origin's storage, whether it is
    closed, and every chord pressed (in the browser's spelling, for a test to read).
    """

    def __init__(
        self,
        site: FakeSite,
        clock: Clock,
        viewport: ScreenSize = FAKE_VIEWPORT,
        file_roots: tuple[Path, ...] = (),
    ) -> None:
        """Open a fake browser on about:blank.

        Args:
            site: The pages it serves.
            clock: Stamps every screenshot.
            viewport: The size of every screenshot.
            file_roots: The only directories a file URL may load from, as for the real browser
                (`BrowserLaunch.file_roots`); empty refuses every file URL.
        """
        self._site = site
        self._clock = clock
        self._viewport = viewport
        self._file_roots = file_roots
        self._page = BLANK_PAGE
        self._values: dict[str, str] = {}
        self._revealed: set[str] = set()
        self._focus: str | None = None
        self._cookies: dict[tuple[str, str], StoredCookie] = {}  # (domain, name) to the cookie.
        self._storage: dict[str, dict[str, str]] = {}  # Origin to its local-storage items.
        self._closed = False
        self.pressed: list[str] = []

    async def navigate(self, url: str) -> None:
        """Load the page at `url`, following its guard; see Browser."""
        self._check_open("navigate")
        self._load(url, "navigate")

    async def click(self, target: ElementTarget) -> None:
        """Click the one visible element `target` names and run its actions; see Browser."""
        element = self._resolve(target, "click")
        self._focus = element.key
        self._run(element.on_click, "click")

    async def fill(self, target: ElementTarget, text: str) -> None:
        """Replace the value of the field `target` names; see Browser."""
        element = self._resolve(target, "fill")
        if not element.is_field:
            raise PeripheralError(PERIPHERAL, "fill", f"{target.describe()} is not a text field")
        self._values[element.key] = text
        self._focus = element.key

    async def press(self, keys: str, target: ElementTarget | None = None) -> None:
        """Press one chord on `target`, or on whatever has focus; see Browser."""
        self._check_open("press")
        try:
            chord = browser_chord(keys)
        except ValueError as error:
            raise PeripheralError(PERIPHERAL, "press", str(error)) from error
        key = self._resolve(target, "press").key if target is not None else self._focus
        self.pressed.append(chord)
        # Enter is the one key a page here reacts to, as a form reacts to it in a field.
        if chord == ENTER and key is not None:
            self._run(self._page.element(key).on_enter, "press")

    async def url(self) -> str:
        """Return the current page's URL; see Browser."""
        self._check_open("read the URL")
        return self._page.url

    async def title(self) -> str:
        """Return the current page's title; see Browser."""
        self._check_open("read the title")
        return self._page.title

    async def snapshot(self) -> str:
        """Return the visible elements as aria-snapshot lines, bounded; see Browser."""
        self._check_open("snapshot")
        lines = [_snapshot_line(e, self._values.get(e.key, "")) for e in self._visible()]
        return bounded("\n".join(lines), MAX_SNAPSHOT_CHARS)

    async def element_text(self, target: ElementTarget) -> str | None:
        """Return the one visible element's text, None when none; see Browser."""
        self._check_open("read an element")
        found = self._matching(target)
        if not found:
            return None
        if len(found) > 1:
            raise ambiguity(target, len(found), "read an element")
        element = found[0]
        return bounded(_read(element, self._values.get(element.key, "")), MAX_ELEMENT_TEXT_CHARS)

    async def page_text(self) -> str:
        """Return the visible elements' text, one per line, bounded; see Browser."""
        self._check_open("read the page")
        texts = [element.text for element in self._visible() if element.text]
        return bounded("\n".join(texts), MAX_PAGE_TEXT_CHARS)

    async def screenshot(self) -> Frame:
        """Return a real PNG in a colour of the current page's own; see Browser."""
        self._check_open("screenshot")
        colour = hashlib.sha256(self._page.url.encode()).digest()[:3]
        png = solid_png(
            self._viewport.width, self._viewport.height, (colour[0], colour[1], colour[2])
        )
        return Frame.from_png(png, self._clock.now())

    async def checkpoint(self) -> BrowserCheckpoint:
        """Take the URL, every cookie and every origin's local storage; see Browser."""
        self._check_open("checkpoint")
        storage = {origin: dict(items) for origin, items in self._storage.items()}
        state = BrowserState(cookies=tuple(self._cookies.values()), local_storage=storage)
        return BrowserCheckpoint(url=self._page.url, state=state.dump())

    async def restore(self, checkpoint: BrowserCheckpoint) -> None:
        """Put back exactly the checkpoint's cookies and storage, then load its URL; see Browser."""
        self._check_open("restore")
        try:
            state = BrowserState.load(checkpoint.state)
        except ValueError as error:
            reason = "the checkpoint was not taken by a browser like this one"
            raise PeripheralError(PERIPHERAL, "restore", reason) from error
        # Replacing wholesale is what removes everything created since the checkpoint.
        self._cookies = {(cookie.domain, cookie.name): cookie for cookie in state.cookies}
        self._storage = {origin: dict(items) for origin, items in state.local_storage.items()}
        self._load(checkpoint.url, "restore")

    async def close(self) -> None:
        """End this client's use of the browser; idempotent. See Browser."""
        self._closed = True

    def _check_open(self, operation: str) -> None:
        """Refuse any operation once closed, as the real browser does."""
        if self._closed:
            raise PeripheralError(PERIPHERAL, operation, "the browser connection is closed")

    def _resolve(self, target: ElementTarget, operation: str) -> FakeElement:
        """Return the one visible element `target` names, or raise as the real browser would."""
        self._check_open(operation)
        found = self._matching(target)
        if not found:
            raise ElementNotFoundError(target.describe(), operation)
        if len(found) > 1:
            raise ambiguity(target, len(found), operation)
        return found[0]

    def _matching(self, target: ElementTarget) -> list[FakeElement]:
        """Return every visible element `target` names."""
        return [element for element in self._visible() if _names(target, element)]

    def _visible(self) -> list[FakeElement]:
        """Return the current page's elements a person could see now."""
        page = self._page
        return [e for e in page.elements if not e.hidden or e.key in self._revealed]

    def _load(self, url: str, operation: str) -> None:
        """Show the page at `url`, following guards, with its fields and reveals reset."""
        # Every load passes here (a navigation, a link's click, a restore), so one check keeps
        # every file URL outside the roots away, exactly where the real browser's route does.
        refuse_outside_roots(url, self._file_roots, operation)
        page = _landing(self._site, self._storage, url, operation)
        self._page = page
        self._values = {e.key: e.value for e in page.elements if e.value is not None}
        self._revealed = set()
        self._focus = None

    def _run(self, actions: tuple[FakeAction, ...], operation: str) -> None:
        """Run an element's actions in order; a navigation ends them."""
        for action in actions:
            match action:
                case Navigate(url=url):
                    self._load(url, operation)
                    return  # The page is gone: nothing after a navigation runs on it.
                case SetCookie(name=name, value=value):
                    cookie = _cookie(self._page.url, name, value)
                    if cookie is not None:
                        self._cookies[(cookie.domain, cookie.name)] = cookie
                case SetStorage(key=key, value=value):
                    origin = storage_origin(self._page.url)
                    if origin is not None:
                        self._storage.setdefault(origin, {})[key] = value
                case Reveal(element=key):
                    self._revealed.add(key)
                case Submit():
                    self._run(
                        action.success if self._accepts(action) else action.failure, operation
                    )
                    return  # A submit hands the page over to its outcome.

    def _accepts(self, submit: Submit) -> bool:
        """Return whether every field a Submit checks holds its expected value."""
        return all(self._values.get(key) == value for key, value in submit.expected)


def _landing(
    site: FakeSite, storage: dict[str, dict[str, str]], url: str, operation: str
) -> FakePage:
    """Return the page a visit to `url` lands on once every guard has sent the visitor on."""
    page = site.page(url)
    # A guard may send the visitor on, and that page may have a guard of its own.
    for _ in range(MAX_GUARD_HOPS):
        if page is None:
            raise PeripheralError(PERIPHERAL, operation, f"the site has no page at {URL_MASK}")
        if page.requires is None or page.otherwise is None or _holds(storage, page):
            return page
        page = site.page(page.otherwise)
    raise PeripheralError(PERIPHERAL, operation, "the page's guards redirect in a loop")


def _names(target: ElementTarget, element: FakeElement) -> bool:
    """Return whether `target` names `element`, exact names compared as Playwright compares."""
    if target.role is not None:
        return element.role == target.role and (
            target.name is None or _same(element.name, target.name)
        )
    if target.label is not None:
        return _same(element.label, target.label)
    if target.text is not None:
        return _same(element.text, target.text)
    return target.selector in element.selectors


def _same(actual: str | None, wanted: str) -> bool:
    """Compare two names or texts exactly, once whitespace is collapsed."""
    return actual is not None and normalised(actual) == normalised(wanted)


def _read(element: FakeElement, value: str) -> str:
    """Return what an element reads as: a field's value (never a password's), else its text."""
    if element.is_field:
        return "" if element.secret else value
    return element.text


def _snapshot_line(element: FakeElement, value: str) -> str:
    """Render one element, with its field value if any, the way an aria snapshot line does."""
    head = f"- {element.role or 'text'}" + (f' "{element.name}"' if element.name else "")
    if element.is_field and value:
        return f"{head}: {REDACTED if element.secret else value}"
    return f"{head}: {element.text}" if element.text and not element.name else head


def _holds(storage: dict[str, dict[str, str]], page: FakePage) -> bool:
    """Return whether `page`'s origin holds the local-storage item its guard requires."""
    origin = storage_origin(page.url)
    items = storage.get(origin, {}) if origin is not None else {}
    return page.requires is not None and bool(items.get(page.requires))


def _cookie(url: str, name: str, value: str) -> StoredCookie | None:
    """Build the cookie a page at `url` sets; None for a page with no host to set one for."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None  # Chromium gives file:// and about:blank pages no cookies.
    return StoredCookie(
        name=name,
        value=value,
        domain=parts.hostname,
        path="/",
        expires=-1,
        http_only=False,
        secure=parts.scheme == "https",
        same_site="Lax",
    )
