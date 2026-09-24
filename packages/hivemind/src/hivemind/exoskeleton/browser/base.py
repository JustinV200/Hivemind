"""Define Browser and BrowserCheckpoint: the Exoskeleton's browser fast path, and its undo point.

The browser fast path (roadmap step 6.11, ADR-0031) drives a Chromium the lease started on the
Cell: navigate, act on an element named by accessible role and name (or label, visible text, CSS
selector), press keys, and read the page back as structure rather than pixels -- its URL, title,
an accessibility-tree snapshot, one element's text, the page's visible text -- so a model with no
vision can still do browser work, and the Capping gate can check URL_MATCHES and ELEMENT_TEXT
postconditions structurally (roadmap step 6.7). `checkpoint`/`restore` are the gate's browser-level
rollback (ADR-0032): the page's URL, cookies and local storage, taken before a GUI action and put
back if its declared postcondition fails. Server-side effects are out of any checkpoint's reach,
which is why a step the bee knows is irreversible is judged before its next step.

`BrowserLauncher` starts the browser for a lease through the Cell's own session, so the process
belongs to the lease like the display and the sound server do (ADR-0031).

Latency classes used below: *interactive* is one round trip to the browser (tens of
milliseconds); *page load* waits for navigation to settle (up to the implementation's navigation
timeout, seconds); *waits for the element* polls up to the implementation's element timeout.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`.
    Implemented by `hivemind.exoskeleton.browser.playwright.PlaywrightBrowser` and
    `hivemind.exoskeleton.browser.fake.FakeBrowser`; called by the Capping gate's GUI surface, the
    `browser_*` tools and the flight recorder; `BrowserLauncher` is implemented by the Playwright
    launcher and the fake, and called by `hivemind.exoskeleton.attach`. Calls into `hivemind.cell`
    (CellSession, BackgroundProcess), `hivemind.exoskeleton.frames`, `.scratch` and waggle's
    ElementTarget only.

Key invariants:
    - Every read that returns page content is bounded by its implementation's own character cap,
      so a huge page never floods a tool result or a recording.
    - `BrowserCheckpoint.state` is opaque outside the implementation that produced it.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why Playwright over CDP.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for the
      checkpoint's role in rollback.
    - waggle.messages.capping.gui for ElementTarget.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import BackgroundProcess, CellSession
from hivemind.exoskeleton.frames import Frame
from hivemind.exoskeleton.scratch import ScratchLayout
from waggle.messages.capping import ElementTarget

__all__ = ["Browser", "BrowserCheckpoint", "BrowserLaunch", "BrowserLauncher", "LaunchedBrowser"]


class BrowserCheckpoint(BaseModel):
    """The browser state a GUI action may undo: the page URL, plus cookies and local storage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(description="The page URL when the checkpoint was taken.")
    state: str = Field(
        repr=False,
        description="The cookies and local storage, serialised by the implementation that took "
        "the checkpoint and read back only by it; may hold session tokens, so never logged.",
    )


class Browser(Protocol):
    """Drive the lease's browser and read its page back as structure."""

    async def navigate(self, url: str) -> None:
        """Load `url` in the page and wait for it to settle.

        Latency: page load. Failure: PeripheralError when navigation fails or times out.

        Args:
            url: Where to go; the caller has already checked the scheme and the network grant.

        Raises:
            PeripheralError: Navigation failed.
        """
        ...

    async def click(self, target: ElementTarget) -> None:
        """Click the element `target` names.

        Latency: waits for the element, then interactive. Failure: ElementNotFoundError when no
        element matches within the wait; PeripheralError for any other browser error.

        Args:
            target: The element, by role and name, label, visible text or selector.

        Raises:
            ElementNotFoundError: No element matches.
            PeripheralError: The click failed.
        """
        ...

    async def fill(self, target: ElementTarget, text: str) -> None:
        """Replace the value of the input `target` names with `text`.

        Latency: waits for the element, then interactive. Failure: as `click`.

        Args:
            target: The input element.
            text: The new value; never logged.

        Raises:
            ElementNotFoundError: No element matches.
            PeripheralError: Filling failed.
        """
        ...

    async def press(self, keys: str, target: ElementTarget | None = None) -> None:
        """Press one key chord in the page, on `target` when given.

        Latency: interactive (waits for `target` first when given). Failure: as `click`.

        Args:
            keys: The chord, in waggle's KEYS_PATTERN syntax ("Enter", "ctrl+a").
            target: The element to press it on; None presses it on the page.

        Raises:
            ElementNotFoundError: `target` matches nothing.
            PeripheralError: The press failed.
        """
        ...

    async def url(self) -> str:
        """Return the page's current URL. Latency: interactive. Failure: PeripheralError."""
        ...

    async def title(self) -> str:
        """Return the page's title. Latency: interactive. Failure: PeripheralError."""
        ...

    async def snapshot(self) -> str:
        """Return the page's accessibility tree as text: roles, names, values, bounded.

        Latency: interactive. Failure: PeripheralError. The snapshot is what a model without
        vision reads to decide what to click next.
        """
        ...

    async def element_text(self, target: ElementTarget) -> str | None:
        """Return the text of the element `target` names, or None when nothing matches.

        Latency: interactive (no wait: None means "not there now"). Failure: PeripheralError.

        Args:
            target: The element to read.
        """
        ...

    async def page_text(self) -> str:
        """Return the page's visible text, bounded.

        Latency: interactive. Failure: PeripheralError.
        """
        ...

    async def screenshot(self) -> Frame:
        """Capture the page's viewport as a Frame.

        Latency: interactive. Failure: PeripheralError.
        """
        ...

    async def checkpoint(self) -> BrowserCheckpoint:
        """Take an undo point: URL, cookies, local storage. Latency: interactive."""
        ...

    async def restore(self, checkpoint: BrowserCheckpoint) -> None:
        """Put a checkpoint back: cookies and local storage restored, page reloaded at its URL.

        Latency: page load. Failure: PeripheralError when the state cannot be applied.

        Args:
            checkpoint: One this same browser's `checkpoint` returned.

        Raises:
            PeripheralError: Restoring failed.
        """
        ...

    async def close(self) -> None:
        """Release this client's connection to the browser; the process itself is not killed.

        Idempotent. Latency: interactive. The browser process belongs to the lease, and detach
        stops it.
        """
        ...


@dataclass(frozen=True, slots=True)
class BrowserLaunch:
    """What a launcher needs to start one lease's browser on its Cell.

    Attributes:
        layout: Where the profile and log go; always inside the lease's scratch.
        headed: Whether the browser shows a window on the display `environment` names; False
            runs it headless (a browser-only task, or a Cell with no display to use).
        sandbox: Whether Chromium keeps its own sandbox. False only where the composition root
            says the Cell is itself the sandbox (a Virtual Cell) or the process runs as root, which
            the sandbox cannot start under (ADR-0031).
        environment: HOME and the XDG directories (`ScratchLayout.home_environment`), plus the
            display's and sound server's variables when the lease has them.
    """

    layout: ScratchLayout
    headed: bool
    sandbox: bool
    environment: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LaunchedBrowser:
    """A browser a launcher started: the client to drive it, and the processes detach stops."""

    browser: Browser  # Connected and on about:blank.
    processes: tuple[BackgroundProcess, ...]  # Every process started for it, oldest first.


class BrowserLauncher(Protocol):
    """Start the lease's browser through the Cell's session and connect a Browser to it."""

    async def launch(self, session: CellSession, request: BrowserLaunch) -> LaunchedBrowser:
        """Start a browser on the Cell and return a connected Browser.

        Latency: seconds (the browser's own start-up), bounded by the implementation's launch
        timeout. Failure: AttachError when no browser can be found or it never becomes ready;
        anything this call started is already stopped when it raises.

        Args:
            session: The Cell's session; the browser process is started through it, so the lease
                knows about it and releases it as a backstop.
            request: Where its files go, headed or headless, sandboxed or not, its environment.

        Returns:
            The connected Browser and the processes started for it.

        Raises:
            AttachError: No browser was found, or it started but never became reachable.
        """
        ...
