"""Script and serve the fixture-site login goal, shared by the phase 6 exit-criteria e2e tests.

`test_phase6_exit_criteria.py` proves the roadmap phase 6 exit criteria (`.claude/roadmap.md`,
after "## Phase 6") against three placements. Every placement logs in to the same fixture site
(`tests/fixtures/sites/login/`, mirrored for the fake browser by
`hivemind.exoskeleton.browser.fake.login`) served over loopback http rather than opened as a
`file://` URL, because ADR-0034 confines a lease's browser to its own scratch and this fixture
site lives beside the tests, not inside any one task's lease. This module holds what every
placement's scenario shares: the http server, the one-subtask login plan (an Exoskeleton subtask
with structural URL_MATCHES/ELEMENT_TEXT acceptance, ADR-0032), the scripted browser tool calls
in both shapes the suite's two `FakeLLMProvider` styles need (a `WorkerTurn` callback for the Hive
Stand's own responder-style provider, and a flat queued script for a Virtual Cell's
`ContainerSpawningFakeCellBackend`), and the checks the exit criteria themselves state: a
recording's HTML carries a before and an after frame per action, and neither the captured logs
nor a trail event's payload ever carries a screenshot's bytes.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used only by
    `tests.e2e.test_phase6_exit_criteria`.

Key invariants:
    - `login_worker_turn` and `login_container_script` build the identical sequence of tool
      calls (`_login_calls`), so the Real Cell and Virtual Cell scenarios exercise the same
      goal; only the scripting mechanism their own provider needs differs.
    - Every scenario issues one browser call per model turn, never several in the same round:
      `ProviderCapabilities.full()` (this suite's own default) declares `parallel_tool_calls
      =True`, and `hivemind.llm.ladders.tools.run_tool_loop` runs a round's calls concurrently
      whenever a binding declares that, which was found here to race a real click against a
      real navigation (a same-round wrong click's own checkpoint landing before the preceding
      correct click's navigation had committed). A model that actually looks at the page between
      steps -- what the Exoskeleton's tools are designed for (ADR-0032's "a page reacts after
      the click returns") -- would never batch these either; one call per round is the realistic
      shape, not only the safe one.
    - A `POSTCONDITION_FAILED` Alarm's default escalation (`RETRY`) detaches and reattaches the
      Exoskeleton and respawns a fresh sub-bee, whatever the bee's own scripted turn already
      did; `login_container_script`'s own `wrong_click=True` branch queues a second, clean
      attempt right after the first so the task still reaches `task.succeeded`.
    - `WORKER_ROLE` is the one place the plan's `role` field is named, so switching the scripted
      plan to the Forager role (the roadmap's own "Later" note) is a one-line change here.

See Also:
    - .claude/roadmap.md phase 6 "Exit criteria" for the three bullets this module supports.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for the
      postcondition kinds and the flight recorder.
    - docs/adr/0034-a-lease-browser-reads-only-its-own-scratch.md for why the fixture site is
      served over http rather than opened as a file:// URL.
    - tests.e2e.kernel_helpers for WorkerTurn, HaikuScript and the tool-call scripting shapes
      this module's own `login_worker_turn` reuses.
    - hivemind.exoskeleton.browser.fake.login for the fixture site's fake-browser mirror and its
      shared names (LOGIN_USERNAME, LOGIN_PASSWORD, WELCOME_HEADING).
"""

from __future__ import annotations

import asyncio
import functools
import importlib.util
import json
import re
import shutil
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from e2e.kernel_helpers import WorkerTurn, text_response, tool_response, tool_round_count

from hivemind.cli.stores import open_recordings
from hivemind.exoskeleton.attach import AUDIO_PROGRAMS, WINDOW_MANAGER, X11_PROGRAMS
from hivemind.exoskeleton.recorder import RecordedAction, RecordingInfo, render_html
from hivemind.llm import LLMRequest, LLMResponse, ToolCall
from hivemind.llm.fake import text_response as fake_text_response
from hivemind.llm.fake import tool_call_response as fake_tool_call_response
from hivemind.pheromone import PheromoneEvent

SITE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sites" / "login"

# roadmap "Later": the orchestrator switches this to "FORAGER" once that role lands; every
# scenario below reads the plan's role from here, so that switch is this one line.
WORKER_ROLE = "DRONE"

# Mirrors hivemind.exoskeleton.browser.fake.login's own fixture names (that module's docstring:
# "every name, title and text here matches the real fixture site's").
LOGIN_USERNAME = "alice"
LOGIN_PASSWORD = "honeycomb"  # noqa: S105 -- the fixture account's password, printed in its README.
WELCOME_HEADING = "Welcome, alice"
# Static markup on welcome.html, present the instant the page loads (unlike the heading, which
# that page's own trailing <script> renames from "Welcome" once it runs): the safe target for a
# click that must resolve immediately after a navigation, before anything else reacts to it.
WELCOME_NOTE = "You are logged in to the HiveMind fixture site."

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"  # The PNG file format's own magic bytes.
PNG_BASE64_PREFIX = "iVBORw0KGgo"  # base64(PNG_SIGNATURE): what a naive dump of a frame looks like.
_IMG_TAG = (
    '<img src="data:image/png;base64,'  # hivemind.exoskeleton.recorder.playback._image's tag.
)

__all__ = [
    "LOGIN_PASSWORD",
    "LOGIN_USERNAME",
    "PNG_BASE64_PREFIX",
    "PNG_SIGNATURE",
    "SITE_DIR",
    "WELCOME_HEADING",
    "WELCOME_NOTE",
    "WORKER_ROLE",
    "assert_no_screenshot_bytes",
    "assert_two_frames_per_action",
    "browser_missing",
    "grant_hive_stand_full_access",
    "login_container_script",
    "login_plan",
    "login_worker_turn",
    "payloads_text",
    "read_recording",
    "serve_login_site",
    "x11_toolchain_missing",
]


class _QuietHandler(SimpleHTTPRequestHandler):
    """Serve the fixture site without a line on stderr per request."""

    def log_message(self, format: str, *args: object) -> None:
        """Say nothing: a test reads the goal's own result, never an access log."""


@contextmanager
def serve_login_site() -> Iterator[str]:
    """Serve `tests/fixtures/sites/login/` on loopback, on a free port; yield its origin.

    ADR-0034: a lease's browser reads only its own scratch, so the fixture site (which lives
    beside the tests, not inside any lease) is opened over http instead of as a `file://` URL.

    Yields:
        The origin (`http://127.0.0.1:<port>`) `login.html`/`welcome.html` are served under.
    """
    handler = functools.partial(_QuietHandler, directory=str(SITE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def login_plan(
    origin: str, *, browser_only: bool = False, role: str = WORKER_ROLE
) -> dict[str, object]:
    """Build the one-subtask plan: log in at `origin` and reach the welcome page.

    Args:
        origin: Where the fixture site is served, from `serve_login_site`.
        browser_only: Whether the subtask needs only the browser fast path (no desktop display);
            the placement (c) exit-criterion variant.
        role: The Worker role the plan names; `WORKER_ROLE` (module docstring's "Later" note).

    Returns:
        A plan dict ready for `HaikuScript(..., plan=...)`: one Exoskeleton subtask whose
        acceptance is checked structurally (URL_MATCHES the welcome page, ELEMENT_TEXT its
        heading), never by a file the Worker writes.
    """
    needs: dict[str, object] = {"exoskeleton": True}
    if browser_only:
        needs["browser_only"] = True
    welcome_url = f"{origin}/welcome.html"
    return {
        "tasks": [
            {
                "key": "login",
                "title": "Log in to the fixture site",
                "objective": (
                    f"Open {origin}/login.html in the browser, fill in the Username and "
                    f"Password fields for {LOGIN_USERNAME}, click Log in, and reach the "
                    "welcome page."
                ),
                "acceptance": [
                    {"kind": "URL_MATCHES", "subject": "page", "argv": [], "expected": welcome_url},
                    {
                        "kind": "ELEMENT_TEXT",
                        "subject": f"role=heading;name={WELCOME_HEADING}",
                        "argv": [],
                        "expected": WELCOME_HEADING,
                    },
                ],
                "needs": needs,
                "clearance": "C1",
                "depends_on": [],
                "role": role,
            }
        ]
    }


def _correct_login_calls(
    login_url: str, welcome_url: str
) -> list[tuple[str, str, dict[str, object]]]:
    """Build the four (call_id, tool, arguments) triples that log in and reach the welcome page."""
    return [
        ("navigate", "browser_navigate", {"url": login_url}),
        (
            "fill_username",
            "browser_fill",
            {"target": {"label": "Username"}, "text": LOGIN_USERNAME},
        ),
        (
            "fill_password",
            "browser_fill",
            {"target": {"label": "Password"}, "text": LOGIN_PASSWORD, "secret": True},
        ),
        (
            "click_submit",
            "browser_click",
            {"target": {"role": "button", "name": "Log in"}, "expect": {"url": welcome_url}},
        ),
    ]


def _wrong_click_call(login_url: str) -> tuple[str, str, dict[str, object]]:
    """Build the deliberately wrong click: a real, always-present target, an impossible `expect`.

    `WELCOME_NOTE` is in the welcome page's markup from the start (module docstring's own note on
    why, versus the heading), so the click applies cleanly; its declared URL can never hold once
    already on the welcome page, so the gate rolls it back and raises an Alarm (ADR-0032).
    """
    return (
        "click_wrong",
        "browser_click",
        {"target": {"text": WELCOME_NOTE}, "expect": {"url": login_url}},
    )


def _login_calls(
    origin: str, *, wrong_click: bool
) -> tuple[tuple[str, str, dict[str, object]], ...]:
    """Build the (call_id, tool, arguments) triples one login attempt's script sends, in order.

    Args:
        origin: Where the fixture site is served.
        wrong_click: Append `_wrong_click_call` after the correct login (module docstring's own
            "Key invariants" note on why this is always the last call, never batched with the
            others).
    """
    login_url, welcome_url = f"{origin}/login.html", f"{origin}/welcome.html"
    calls = _correct_login_calls(login_url, welcome_url)
    if wrong_click:
        calls.append(_wrong_click_call(login_url))
    return tuple(calls)


def login_worker_turn(origin: str, *, wrong_click: bool = False) -> WorkerTurn:
    """Build a `WorkerTurn` for `HaikuScript`: one browser call per round, then a closing line.

    Args:
        origin: Where the fixture site is served.
        wrong_click: Forwarded to `_login_calls`.

    Returns:
        A callback answering every `ModelSlot.WORKER` request on a responder-style provider
        (the Hive Stand's own `FakeLLMProvider`, which never runs out of scripted turns).
    """
    calls = _login_calls(origin, wrong_click=wrong_click)

    def _turn(request: LLMRequest) -> LLMResponse:
        # tool_round_count reads the number of tool results already in the conversation, which
        # is exactly the index of the next scripted call once every round makes just one
        # (_login_calls's own module note on why never more than one).
        next_call = tool_round_count(request)
        if next_call < len(calls):
            return tool_response(request, (calls[next_call],))
        return text_response("Logged in.")

    return _turn


def _attempt_script(origin: str, *, wrong_click: bool) -> tuple[LLMResponse, ...]:
    """Build one attempt's own queued responses: one call per round, then the closing line."""
    calls = _login_calls(origin, wrong_click=wrong_click)
    steps = tuple(
        fake_tool_call_response(ToolCall(id=call_id, name=name, arguments=arguments))
        for call_id, name, arguments in calls
    )
    return (*steps, fake_text_response("Logged in."))


def login_container_script(origin: str, *, wrong_click: bool = False) -> tuple[LLMResponse, ...]:
    """Build the fixed, queued script a Virtual Cell's own `FakeLLMProvider` answers from.

    `ContainerSpawningFakeCellBackend`'s own provider (`builders.virtual_cells`) is scripted
    through `FakeLLMProvider.script(...)`, a flat FIFO queue rather than a responder callback
    (`login_worker_turn`'s own shape): one response per browser call, each its own round, then
    the closing line.

    A `POSTCONDITION_FAILED` Alarm's default escalation (`RETRY`, roadmap step 4.10's own
    policy table) detaches and reattaches the Exoskeleton and respawns a fresh sub-bee for the
    task, whatever the bee's own turn already did (observed here: it still runs after the bee's
    own scripted "Logged in." closing line). `wrong_click=True` therefore queues a second
    attempt's own clean script (no wrong click) right after the first's, so the task still
    reaches `task.succeeded` through that respawn instead of stalling on an empty queue.

    Args:
        origin: Where the fixture site is served.
        wrong_click: Forwarded to `_login_calls`, for the first attempt only.
    """
    first_attempt = _attempt_script(origin, wrong_click=wrong_click)
    if not wrong_click:
        return first_attempt
    second_attempt = _attempt_script(origin, wrong_click=False)
    return (*first_attempt, *second_attempt)


def grant_hive_stand_full_access(manifest_path: Path) -> None:
    """Patch `[hive_stand] access_level = "FULL"` onto an already-written `fake_manifest`.

    `AccessLevel.SCRATCH` (the manifest's own default) grants `exoskeleton:browser` only
    (`hivemind.guard.access.ceiling_for`); a desktop need also needs `exoskeleton:display`,
    which only `FULL`'s ceiling carries. Patches the file the way
    `e2e.kernel_helpers.set_budget_fraction` does, rather than growing `fake_manifest`'s own
    signature past codingrules 5.1's parameter cap.

    Args:
        manifest_path: The manifest `fake_manifest` already wrote.
    """
    text = manifest_path.read_text(encoding="utf-8")
    patched = text.replace("[hive_stand]\n", '[hive_stand]\naccess_level = "FULL"\n', 1)
    manifest_path.write_text(patched, encoding="utf-8")


def x11_toolchain_missing() -> str | None:
    """Return why the real X11/audio toolchain cannot run here, or None when it can.

    Checked once per test rather than once per module import (codingrules 5.5: no side effects
    at import time); mirrors `tests.contracts.exoskeleton_harness.RealDesktopHarness.missing`.
    """
    absent = [
        p for p in (*X11_PROGRAMS, WINDOW_MANAGER, *AUDIO_PROGRAMS) if shutil.which(p) is None
    ]
    return f"not installed here: {', '.join(absent)}" if absent else None


def browser_missing() -> str | None:
    """Return why Playwright cannot run here, or None when it can.

    A cheap module-presence check, never an import of `hivemind.exoskeleton.browser.playwright`
    itself (mirrors `tests.contracts.browser_harness.RealBrowserHarness.missing`), so this reads
    true even in an environment that has never tried to launch a browser.
    """
    if importlib.util.find_spec("playwright") is None:
        return "Playwright is not installed here (the hivemind[browser] extra)"
    return None


def read_recording(db_path: Path, cell_id: str) -> tuple[RecordingInfo, tuple[RecordedAction, ...]]:
    """Open the Hive's own database and return its one flight recording for `cell_id`.

    `open_recordings` and the store it returns each run their own `asyncio.run`
    (`hivemind.cli.stores`'s own contract), so this must be called from plain sync code, never
    from inside the Hive's own running event loop.

    Args:
        db_path: The Hive's SQLite database file (`[hive] db` in the manifest).
        cell_id: The Cell whose one recording this reads.

    Returns:
        The recording's header and its actions, oldest first.

    Raises:
        AssertionError: No recording exists for `cell_id`.
    """
    store = open_recordings(db_path)
    headers = asyncio.run(store.recordings(cell_id=cell_id))
    assert headers, f"no flight recording for Cell {cell_id}"
    info = headers[0]
    actions = asyncio.run(store.actions(info.recording_id))
    return info, actions


def assert_two_frames_per_action(info: RecordingInfo, actions: Sequence[RecordedAction]) -> None:
    """Render `actions` to the exported HTML and assert every action carries both frames.

    Roadmap phase 6 exit criterion: "the login's recording plays back with before and after
    frames per action." Calls `render_html`, the function `hive recordings export` itself calls.

    Args:
        info: The recording's header.
        actions: Its actions, oldest first.

    Raises:
        AssertionError: An action's section is missing, or does not carry exactly two frames.
    """
    html = render_html(info, actions)
    sections = re.findall(r'<section id="action-\d+">.*?</section>', html, re.DOTALL)
    assert len(sections) == len(actions), "one <section> per recorded action"
    for number, section in enumerate(sections, start=1):
        count = section.count(_IMG_TAG)
        assert count == 2, f"action {number}: expected a before and an after frame, found {count}"


def payloads_text(events: Sequence[PheromoneEvent]) -> str:
    """Render every trail event's payload as one string, for the no-screenshot-bytes grep.

    Args:
        events: The trail events to render.

    Returns:
        Every payload, JSON-dumped, one per line.
    """
    return "\n".join(json.dumps(event.payload, default=str) for event in events)


def assert_no_screenshot_bytes(text: str, where: str) -> None:
    """Assert `text` carries no PNG signature and no base64-encoded PNG prefix.

    Roadmap phase 6 exit criterion: "no screenshot bytes in logs or on the trail." Checked as
    both the raw magic bytes (in case a frame ever reached a log as a byte-string repr) and its
    base64 encoding (how the flight recorder's own exported HTML embeds a frame, `PNG_BASE64_
    PREFIX`), since a log line is text either way.

    Args:
        text: The captured text to check (structlog output, or every trail payload rendered).
        where: What `text` is, named in the assertion message.

    Raises:
        AssertionError: `text` carries either form.
    """
    assert PNG_BASE64_PREFIX not in text, f"found a base64-encoded PNG prefix in {where}"
    encoded = text.encode("utf-8", errors="surrogateescape")
    assert PNG_SIGNATURE not in encoded, f"found the PNG signature bytes in {where}"
