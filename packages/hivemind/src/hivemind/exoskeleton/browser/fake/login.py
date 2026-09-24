"""Build the fixture login site as a FakeSite: login_site(), and the facts every test checks.

Phase 6's exit scenario, and the browser contract suite, log in to one fixture site: a login page
with a "Username" field, a "Password" field and a "Log in" button; a page behind it greeting the
user; a page far longer than any read returns whole. The real site is static HTML under
`packages/hivemind/tests/fixtures/sites/login/` (opened as file:// or served over http); this
module builds the same site for `FakeBrowser`, with the same accessible names, titles and
behaviour: the right credentials store a session item (and, where the page has a host, a cookie)
and go to the welcome page, whose heading reads "Welcome, alice"; wrong ones show an alert and
stay; the help link reveals a hidden line; the welcome page sends a visitor without a session back
to log in. The credentials are a fixture's, printed in the site's README, never a real account.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.fake`. Used by the browser contract suite, unit tests and demos.
    Calls into `.site` only.

Key invariants:
    - Every name, title and text here matches the real fixture site's; change them together.
    - The site's URLs are `<origin>/login`, `<origin>/welcome` and `<origin>/long`.

See Also:
    - packages/hivemind/tests/fixtures/sites/login/README.md for the real site.
    - hivemind.exoskeleton.browser.fake.site for the values this builds.
"""

from __future__ import annotations

from hivemind.exoskeleton.browser.fake.site import (
    FakeElement,
    FakePage,
    FakeSite,
    Navigate,
    Reveal,
    SetCookie,
    SetStorage,
    Submit,
)

FIXTURE_ORIGIN = "https://fixture.test"  # A reserved test domain (RFC 2606): never a real host.
LOGIN_USERNAME = "alice"  # The fixture's one account; printed in the site's README.
LOGIN_PASSWORD = "honeycomb"  # noqa: S105 -- the fixture account's password, in its README.
SESSION_KEY = "session"  # The local-storage item (and cookie) a login sets, holding the user.
LOGIN_TITLE = "Log in - HiveMind fixture"
WELCOME_TITLE = "Welcome - HiveMind fixture"
LONG_TITLE = "Long page - HiveMind fixture"
LOGIN_HEADING = "Log in"  # The login page's heading, and also its button's name.
WELCOME_HEADING = f"Welcome, {LOGIN_USERNAME}"  # What the page behind the login greets with.
WRONG_CREDENTIALS = "Wrong username or password"  # The alert a failed login shows.
HELP_LINK = "Forgot your password?"  # The link that reveals HELP_TEXT.
HELP_TEXT = "Ask the beekeeper for a new one."  # Hidden until the help link is clicked.
LONG_HEADING = "A long page"
LONG_TEXT = "The hive hums along. " * 4_000  # 84,000 characters: past every read's cap.

__all__ = [
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
    "SESSION_KEY",
    "WELCOME_HEADING",
    "WELCOME_TITLE",
    "WRONG_CREDENTIALS",
    "login_site",
]


def login_site(origin: str = FIXTURE_ORIGIN) -> FakeSite:
    """Build the fixture login site under `origin`.

    Args:
        origin: Scheme and host the pages live under, with no trailing slash.

    Returns:
        The site: `<origin>/login`, `<origin>/welcome` (guarded by the session item) and
        `<origin>/long`.

    Example:
        >>> sorted(page.url for page in login_site().pages)
        ['https://fixture.test/login', 'https://fixture.test/long', 'https://fixture.test/welcome']
    """
    login_url, welcome_url = f"{origin}/login", f"{origin}/welcome"
    return FakeSite(
        pages=(
            _login_page(login_url, welcome_url),
            FakePage(
                url=welcome_url,
                title=WELCOME_TITLE,
                elements=(
                    _heading(WELCOME_HEADING, "#greeting"),
                    FakeElement(
                        "note",
                        text="You are logged in to the HiveMind fixture site.",
                        selectors=_css("p"),
                    ),
                ),
                requires=SESSION_KEY,
                otherwise=login_url,
            ),
            FakePage(
                url=f"{origin}/long",
                title=LONG_TITLE,
                elements=(
                    _heading(LONG_HEADING, "h1"),
                    FakeElement(
                        "long", role="paragraph", text=LONG_TEXT, selectors=_css("#long", "p")
                    ),
                ),
            ),
        )
    )


def _login_page(login_url: str, welcome_url: str) -> FakePage:
    """Build the login page: two fields, a button, a help link and a hidden alert."""
    # The form's submit handler: the right credentials log in, anything else shows the alert.
    submit = Submit(
        expected=(("username", LOGIN_USERNAME), ("password", LOGIN_PASSWORD)),
        success=(
            SetStorage(SESSION_KEY, LOGIN_USERNAME),
            SetCookie(SESSION_KEY, LOGIN_USERNAME),
            Navigate(welcome_url),
        ),
        failure=(Reveal("alert"),),
    )
    return FakePage(
        url=login_url,
        title=LOGIN_TITLE,
        elements=(
            _heading(LOGIN_HEADING, "h1"),
            FakeElement("username-label", text="Username", selectors=_css("label")),
            _field("username", "Username", secret=False, submit=submit),
            FakeElement("password-label", text="Password", selectors=_css("label")),
            _field("password", "Password", secret=True, submit=submit),
            FakeElement(
                "submit",
                role="button",
                name=LOGIN_HEADING,
                text=LOGIN_HEADING,
                selectors=_css("#submit", "button"),
                on_click=(submit,),
            ),
            FakeElement(
                "help-link",
                role="link",
                name=HELP_LINK,
                text=HELP_LINK,
                selectors=_css("#help-link", "a"),
                on_click=(Reveal("help"),),
            ),
            FakeElement("help", text=HELP_TEXT, selectors=_css("#help"), hidden=True),
            FakeElement(
                "alert",
                role="alert",
                text=WRONG_CREDENTIALS,
                selectors=_css("[role=alert]"),
                hidden=True,
            ),
        ),
    )


def _field(key: str, label: str, *, secret: bool, submit: Submit) -> FakeElement:
    """Build one of the login form's text fields; Enter in either submits the form."""
    selectors = _css(f"#{key}", "input", *(("input[type=password]",) if secret else ()))
    return FakeElement(
        key,
        role="textbox",
        name=label,
        label=label,
        selectors=selectors,
        value="",
        secret=secret,
        on_enter=(submit,),
    )


def _heading(text: str, *selectors: str) -> FakeElement:
    """Build a page's level-one heading."""
    return FakeElement(
        "heading", role="heading", name=text, text=text, selectors=_css("h1", *selectors)
    )


def _css(*selectors: str) -> frozenset[str]:
    """Collect the CSS selectors that would match an element in the real site."""
    return frozenset(selectors)
