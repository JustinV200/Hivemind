"""Script a Forager on the Hive Stand's fake browser that needs a tool no one has built yet.

Roadmap phase 9's first exit criterion names a Forager. A Forager is the see/act role: it needs an
attached Exoskeleton, here the in-memory login site of `hivemind.exoskeleton.browser.fake`, so a
whole Hive built with `build_hive` runs one with no browser installed. `pin_fake_browser` makes the
Hive Stand report a browser and launch that site (a launcher that starts no process: the stock
fake launcher asks the session to start a stand-in program, which a real Hive Stand has none of).
`forager_plan` is the one task (log in, keep the page's text, count its lines); `forager_turn` is
the Forager's script: log in, keep a file, ask for the line counter, and once the Warden hands it
over, call it and write what it counted.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    tests/e2e/test_phase9_exit_criteria.py.

Key invariants:
    - The script reads what the request offers, never a call counter alone, because a promotion
      restarts the tool loop from state: the page the Forager logged in on is still open then.
    - The plan's acceptance is structural (URL and heading, checked by the Warden on the browser)
      plus the answer file, so the task succeeds only if the new tool really ran.

See Also:
    - tests.e2e.test_scout_then_forager for the same fake site driven without a Hive.
    - tests.e2e.royal_jelly_runs for the scaffolder script and the shared names.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from e2e.kernel_helpers import text_response, tool_response, tool_round_count
from e2e.royal_jelly_runs import (
    ANSWER_FILE,
    NOTES_FILE,
    NOTES_NAME,
    NOTES_TEXT,
    TOOL_NAME,
    ask_arguments,
    tool_names,
    use_turn,
    write_arguments,
)

from hivemind.exoskeleton.browser.base import BrowserLaunch, LaunchedBrowser
from hivemind.exoskeleton.browser.fake import (
    LOGIN_HEADING,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    WELCOME_HEADING,
    FakeBrowser,
    login_site,
)
from hivemind.llm import LLMRequest, LLMResponse
from waggle.clock import Clock
from waggle.messages.capping import ElementTarget

ORIGIN = "http://127.0.0.1:8000"  # A loopback origin: the fake site never leaves the Cell.
LOGIN_URL = f"{ORIGIN}/login"
_BROWSER_NEED = {"exoskeleton": True, "browser_only": True}
_GREETING = ElementTarget(role="heading", name=WELCOME_HEADING)

__all__ = ["FORAGER_TITLE", "forager_plan", "forager_turn", "pin_fake_browser"]

FORAGER_TITLE = "Log in and count the lines of what was kept"


class _InProcessLauncher:
    """Launch the fake login site as an in-memory browser; no process is started."""

    def __init__(self, clock: Clock) -> None:
        """Serve `login_site` on `ORIGIN`, stamping screenshots with `clock`."""
        self._clock = clock

    async def launch(self, session: object, request: BrowserLaunch) -> LaunchedBrowser:
        """Return a browser on about:blank over the fake site; `session` is unused."""
        del session
        browser = FakeBrowser(login_site(ORIGIN), self._clock, file_roots=request.file_roots)
        return LaunchedBrowser(browser=browser, processes=())


def pin_fake_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the Hive Stand report a browser, and give its Warden the in-memory login site."""
    monkeypatch.setattr("hivemind.cell.local.probe._has_playwright_chromium", lambda: True)
    monkeypatch.setattr(
        "hivemind.cli.compose.exoskeleton.build_browser_launcher",
        lambda clock, _window: _InProcessLauncher(clock),
    )


def forager_plan() -> dict[str, object]:
    """Plan the Forager's one task: log in, keep the page's text, and report its line count."""
    welcome = {
        "kind": "URL_MATCHES",
        "subject": "page",
        "argv": [],
        "expected": f"{ORIGIN}/welcome*",
    }
    greeted = {
        "kind": "ELEMENT_TEXT",
        "subject": _GREETING.subject(),
        "argv": [],
        "expected": WELCOME_HEADING,
    }
    answered: dict[str, object] = {
        "kind": "FILE_EXISTS",
        "subject": ANSWER_FILE,
        "argv": [],
        "expected": None,
    }
    return {
        "tasks": [
            {
                "key": "login",
                "title": FORAGER_TITLE,
                "objective": f"Log in at {LOGIN_URL} as {LOGIN_USERNAME}, "
                f"password {LOGIN_PASSWORD} "
                f"(a secret), keep the page's text in {NOTES_FILE} and write its line count to "
                f"{ANSWER_FILE}.",
                "acceptance": [welcome, greeted, answered],
                "needs": _BROWSER_NEED,
                "clearance": "C1",
                "role": "FORAGER",
                "depends_on": [],
            }
        ]
    }


def _login_calls() -> tuple[tuple[str, str, Mapping[str, object]], ...]:
    """The Forager's four browser calls: open the form, fill both fields, click Log in."""
    password = {"target": {"label": "Password"}, "text": LOGIN_PASSWORD, "secret": True}
    submit = {"target": {"role": "button", "name": LOGIN_HEADING}}
    return (
        ("nav", "browser_navigate", {"url": LOGIN_URL}),
        ("user", "browser_fill", {"target": {"label": "Username"}, "text": LOGIN_USERNAME}),
        ("pass", "browser_fill", password),
        ("go", "browser_click", {**submit, "expect": {"url": f"{ORIGIN}/welcome*"}}),
    )


def forager_turn(request: LLMRequest) -> LLMResponse:
    """The Forager script: log in, keep a file, ask for the tool, then use it once it is offered."""
    rounds = tool_round_count(request)
    if TOOL_NAME in tool_names(request):
        return use_turn(request, rounds, TOOL_NAME)
    calls = _login_calls()
    if rounds < len(calls):
        return tool_response(request, (calls[rounds],))
    if rounds == len(calls):
        return tool_response(
            request, (("notes", "write_file", write_arguments(NOTES_FILE, NOTES_TEXT)),)
        )
    if rounds == len(calls) + 1:
        return tool_response(request, (("ask", "request_tool", ask_arguments(TOOL_NAME, "hive")),))
    return text_response(f"No tool arrived; {NOTES_NAME} was not counted.")
