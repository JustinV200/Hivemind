"""Define FakeSite: an in-memory web site, pages by URL, that the fake browser serves.

The fake browser (`browser.fake.browser.FakeBrowser`) must behave like the real one on the
clauses the browser contract suite states, without a browser or a server. A `FakeSite` is the data
it serves: `FakePage`s keyed by URL, each a title and a list of `FakeElement`s, every element
carrying each way a target can name it (role and accessible name, label, visible text, the CSS
selectors that would match it), whether it is a text field (and a password), whether it starts
hidden, and what clicking it or pressing Enter in it does. Those actions are plain values: load a
page, set a cookie or a local-storage item on the page's origin, reveal a hidden element, or
check the page's fields against expected values and then run one list of actions or another (a
form's submit handler). A page may also require a local-storage item on its origin and send a
visitor without it elsewhere, the way a protected page sends a visitor back to its login. Nothing
here runs anything; the fake browser interprets these values.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.fake`. Built by `.login.login_site` and by tests; read by
    `.browser.FakeBrowser`. Calls into the standard library only.

Key invariants:
    - Element keys are unique within a page; a page's guard names both the item and the page to
      send a visitor to, or neither.
    - Every value here is frozen: a site never changes while a browser serves it.

See Also:
    - hivemind.exoskeleton.browser.fake.browser for the browser that serves a FakeSite.
    - hivemind.exoskeleton.browser.fake.login for the fixture login site.
"""

from __future__ import annotations

from dataclasses import dataclass

BLANK_URL = "about:blank"  # Where every browser starts; no site needs to declare it.

__all__ = [
    "BLANK_PAGE",
    "BLANK_URL",
    "FakeAction",
    "FakeElement",
    "FakePage",
    "FakeSite",
    "Navigate",
    "Reveal",
    "SetCookie",
    "SetStorage",
    "Submit",
]


@dataclass(frozen=True, slots=True)
class Navigate:
    """Load another page; nothing listed after a navigation runs, its page is gone."""

    url: str  # Where to go; a URL the site serves.


@dataclass(frozen=True, slots=True)
class SetCookie:
    """Set a cookie for the current page's host (ignored on a page with none, as in Chromium)."""

    name: str  # The cookie's name.
    value: str  # Its value.


@dataclass(frozen=True, slots=True)
class SetStorage:
    """Set one local-storage item on the current page's origin."""

    key: str  # The item's key.
    value: str  # Its value.


@dataclass(frozen=True, slots=True)
class Reveal:
    """Show an element of the current page that started hidden."""

    element: str  # The element's key on the current page.


@dataclass(frozen=True, slots=True)
class Submit:
    """Check the page's fields, then run the success actions or the failure ones."""

    expected: tuple[tuple[str, str], ...]  # (field key, the value it must hold) for every check.
    success: tuple[FakeAction, ...]  # What happens when every field holds its expected value.
    failure: tuple[FakeAction, ...] = ()  # What happens otherwise.


FakeAction = Navigate | SetCookie | SetStorage | Reveal | Submit  # Everything an element can do.


@dataclass(frozen=True, slots=True)
class FakeElement:
    """One element of a fake page and every way a target can name it."""

    key: str  # Unique within its page; how actions and a Submit refer to it.
    role: str | None = None  # Its ARIA role ("button", "textbox", "heading", "alert").
    name: str | None = None  # Its accessible name, which a role target may give.
    label: str | None = None  # The text of the label of this form control.
    text: str = ""  # Its visible text; what a text target and the page's text read.
    selectors: frozenset[str] = frozenset()  # Every CSS selector that would match it, verbatim.
    value: str | None = None  # A text field's starting value; None: not a field.
    secret: bool = False  # A password field: its value never reads back.
    hidden: bool = False  # Present but not shown, so no target finds it, until a Reveal.
    on_click: tuple[FakeAction, ...] = ()  # What clicking it does.
    on_enter: tuple[FakeAction, ...] = ()  # What pressing Enter in it does.

    @property
    def is_field(self) -> bool:
        """Whether text can be filled into it."""
        return self.value is not None


@dataclass(frozen=True, slots=True)
class FakePage:
    """One page of a fake site: its URL, title, elements and, optionally, a guard."""

    url: str  # Its address; the key the site serves it under.
    title: str  # What the browser reports as its title.
    elements: tuple[FakeElement, ...] = ()  # In page order, as a snapshot lists them.
    requires: str | None = None  # A local-storage key its origin must hold to show it...
    otherwise: str | None = None  # ...or the URL a visitor without it is sent to instead.

    def __post_init__(self) -> None:
        """Refuse duplicate element keys and a guard missing one of its halves."""
        keys = [element.key for element in self.elements]
        if len(keys) != len(set(keys)):
            raise ValueError(f"The fake page {self.url} has two elements with one key.")
        if (self.requires is None) != (self.otherwise is None):
            raise ValueError(f"The fake page {self.url} needs both `requires` and `otherwise`.")

    def element(self, key: str) -> FakeElement:
        """Return the element with `key`.

        Args:
            key: An element key on this page.

        Returns:
            The element.

        Raises:
            KeyError: No element on this page has that key (a broken fixture).
        """
        for element in self.elements:
            if element.key == key:
                return element
        raise KeyError(f"The fake page {self.url} has no element {key!r}.")


BLANK_PAGE = FakePage(url=BLANK_URL, title="")  # The page every fake browser opens on.


@dataclass(frozen=True, slots=True)
class FakeSite:
    """A fake web site: the pages a FakeBrowser serves, by URL."""

    pages: tuple[FakePage, ...]  # Every page; no two share a URL.

    def page(self, url: str) -> FakePage | None:
        """Return the page at `url` (fragment ignored), about:blank included, or None.

        Args:
            url: The address to load.

        Returns:
            The page, or None when the site has none there.
        """
        address = url.split("#", 1)[0]
        if address == BLANK_URL:
            return BLANK_PAGE
        return next((page for page in self.pages if page.url == address), None)
