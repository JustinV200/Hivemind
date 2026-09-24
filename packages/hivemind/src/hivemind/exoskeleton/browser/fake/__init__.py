"""Provide the fake browser fast path: a FakeSite, a FakeBrowser serving it, and its launcher.

The browser contract suite runs every clause over the real Playwright browser and over this fake
(codingrules 14.3), and tests and demos everywhere use it instead of starting a Chromium. A
`FakeSite` is a web site as data (pages by URL, elements and what they do); a `FakeBrowser` serves
one with the real browser's strictness, caps and checkpoint format; a `FakeBrowserLauncher` starts a
stand-in process through the Cell's session and returns a FakeBrowser, so attach and detach see a
real launch's shape. `login_site` builds the fixture login site the contract suite and phase 6's
exit scenario use. Shipped code, not test-only (codingrules 14.4).

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser`. Used by the contract suite, unit tests and demos. Calls into
    `hivemind.cell` and the browser package's shared modules; never into Playwright.

Key invariants:
    - Every name in `__all__` is re-exported from one module of this package; this file holds no
      logic (codingrules 5.4).

See Also:
    - hivemind.exoskeleton.browser.base for the protocols these fakes implement.
    - packages/hivemind/tests/contracts/test_browser_contract.py for the clauses they share.

Public API:
    - FakeSite, FakePage, FakeElement, BLANK_PAGE, BLANK_URL: a site as data (site).
    - Navigate, SetCookie, SetStorage, Reveal, Submit, FakeAction: what an element does (site).
    - FakeBrowser, FAKE_VIEWPORT, MAX_GUARD_HOPS: the Browser serving a FakeSite (browser).
    - FakeBrowserLauncher, FAKE_BROWSER_PROGRAM, FAKE_LOG_FILE: its BrowserLauncher (launcher).
    - login_site and its facts (FIXTURE_ORIGIN, LOGIN_USERNAME, LOGIN_PASSWORD, SESSION_KEY, the
      titles, headings and texts): the fixture login site (login).
"""

from hivemind.exoskeleton.browser.fake.browser import FAKE_VIEWPORT, MAX_GUARD_HOPS, FakeBrowser
from hivemind.exoskeleton.browser.fake.launcher import (
    FAKE_BROWSER_PROGRAM,
    FAKE_LOG_FILE,
    FakeBrowserLauncher,
)
from hivemind.exoskeleton.browser.fake.login import (
    FIXTURE_ORIGIN,
    HELP_LINK,
    HELP_TEXT,
    LOGIN_HEADING,
    LOGIN_PASSWORD,
    LOGIN_TITLE,
    LOGIN_USERNAME,
    LONG_HEADING,
    LONG_TEXT,
    LONG_TITLE,
    SESSION_KEY,
    WELCOME_HEADING,
    WELCOME_TITLE,
    WRONG_CREDENTIALS,
    login_site,
)
from hivemind.exoskeleton.browser.fake.site import (
    BLANK_PAGE,
    BLANK_URL,
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

__all__ = [
    "BLANK_PAGE",
    "BLANK_URL",
    "FAKE_BROWSER_PROGRAM",
    "FAKE_LOG_FILE",
    "FAKE_VIEWPORT",
    "FIXTURE_ORIGIN",
    "HELP_LINK",
    "HELP_TEXT",
    "LOGIN_HEADING",
    "LOGIN_PASSWORD",
    "LOGIN_TITLE",
    "LOGIN_USERNAME",
    "LONG_HEADING",
    "LONG_TEXT",
    "LONG_TITLE",
    "MAX_GUARD_HOPS",
    "SESSION_KEY",
    "WELCOME_HEADING",
    "WELCOME_TITLE",
    "WRONG_CREDENTIALS",
    "FakeAction",
    "FakeBrowser",
    "FakeBrowserLauncher",
    "FakeElement",
    "FakePage",
    "FakeSite",
    "Navigate",
    "Reveal",
    "SetCookie",
    "SetStorage",
    "Submit",
    "login_site",
]
