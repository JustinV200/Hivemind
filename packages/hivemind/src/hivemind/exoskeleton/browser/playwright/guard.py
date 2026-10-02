"""Keep a lease's Chromium inside its own files: a route that answers every file:// request.

A page can reach the Cell's disk in more ways than a navigation the bee proposes: a link it
clicks, a script that sets `location`, an iframe, image or stylesheet the page embeds. Once a
Playwright route covers `file://` URLs, Chromium asks before loading any of them, the main frame's
and every subresource's alike, and hands over the URL already canonicalised (checked on this
repository's Chromium, 2026-09-24: a direct navigation, a percent-encoded `..`, an iframe and a
clicked link all arrived at the route). `FileGuard` answers each request: continue when the file
lies inside the lease's file roots, both as written (`hivemind.guard.file_urls`) and once symlinks
are resolved, so a link planted in scratch cannot lead out, and names a file with no other hard
link (ADR-0058): realpath cannot see a hard link, so a second name for a file outside scratch
would otherwise pass as a file inside it. Abort it as blocked by the client otherwise, which
leaves Chromium's own error page where the file would have been, and report the refusal so attach
can put it on the trail. Nothing outside the roots is ever read, so no later read, screenshot or
recording can show it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Installed by `.page.connect_browser` on the
    browser's context before the first page loads. Calls into Playwright, `hivemind.guard`,
    `browser.hosts` (the refusal record), `hivemind.common.logging` and the standard library.

Key invariants:
    - Every file request is answered exactly once; one this guard cannot answer in time, or whose
      path it cannot resolve, is aborted, never continued.
    - Nothing is logged about a request beyond the fact of a refusal: never its URL or path.
    - A file with more than one hard link is refused even inside the roots: its other name may
      lie anywhere. A directory (whose link count is its subdirectories) is judged by path alone.

See Also:
    - hivemind.guard.file_urls for the lexical rule, shared with the tool and the gate.
    - hivemind.exoskeleton.browser.files for the refusal a direct navigation gets first.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path, PurePath

from playwright.async_api import BrowserContext, Route
from playwright.async_api import Error as PlaywrightError

from hivemind.common.logging import get_logger
from hivemind.exoskeleton.browser.hosts import BlockRule, BlockSink, BrowserBlock
from hivemind.guard import file_url_escapes, file_url_path, is_within

FILE_URLS = "file://**"  # Every file URL Chromium requests, in Playwright's glob syntax.
# Resolving a path is one readlink walk on a local disk: milliseconds. The bound only exists so a
# hung filesystem refuses the request instead of pausing the page forever.
RESOLVE_TIMEOUT_S = 2.0
BLOCKED = "blockedbyclient"  # Chromium's own "blocked" error: its error page, no content.

log = get_logger(__name__)

__all__ = ["BLOCKED", "FILE_URLS", "RESOLVE_TIMEOUT_S", "FileGuard"]


class FileGuard:
    """Answer every file:// request a lease's browser makes: continue inside its roots only.

    Owns mutable state (codingrules 8.5): the roots with symlinks resolved, filled once by
    `resolve` (which `install` calls) before any request can arrive; until then it allows nothing.
    """

    def __init__(self, roots: tuple[Path, ...], on_blocked: BlockSink | None = None) -> None:
        """Build the guard for one browser.

        Args:
            roots: The browser's file roots (`BrowserLaunch.file_roots`); empty refuses every
                file request.
            on_blocked: Where each refusal is reported; None reports it to the log alone.
        """
        self._roots = roots
        self._on_blocked = on_blocked
        self._resolved_roots: tuple[Path, ...] = ()

    async def resolve(self) -> None:
        """Resolve the roots' symlinks once, so a resolved file is compared with resolved roots.

        Latency: one readlink walk per root on the local disk, milliseconds.

        Raises:
            TimeoutError: A root could not be resolved within RESOLVE_TIMEOUT_S.
        """
        # External await: resolving touches the disk, so it runs off the event loop, bounded.
        async with asyncio.timeout(RESOLVE_TIMEOUT_S):
            self._resolved_roots = await asyncio.to_thread(_resolve_all, self._roots)

    async def install(self, context: BrowserContext) -> None:
        """Resolve the roots, then route every file request of `context` through this guard.

        Latency: one round trip to the browser, plus resolving each root on the local disk.

        Args:
            context: The browser context every page of the lease's browser lives in.

        Raises:
            PlaywrightError: The route could not be installed; the caller fails the attach.
            TimeoutError: A root could not be resolved within RESOLVE_TIMEOUT_S.
        """
        await self.resolve()
        # External await: one round trip to the browser to start routing file requests.
        await context.route(FILE_URLS, self.decide)

    async def decide(self, route: Route) -> None:
        """Continue one file request inside the roots, abort it otherwise; never raise.

        Args:
            route: The paused request Chromium asked about.
        """
        allowed = await self.allows(route.request.url)
        try:
            # External await: one round trip to the browser to release or refuse the request.
            if allowed:
                await route.continue_()
                return
            await route.abort(BLOCKED)
        except PlaywrightError as error:
            # The page closed or the browser went away while deciding: nothing is left to answer.
            log.debug("exoskeleton.browser_file_route_gone", reason=type(error).__name__)
            return
        log.info("exoskeleton.browser_file_blocked")
        if self._on_blocked is not None:
            # External await: the sink records on the trail, after the request was answered.
            await self._on_blocked(BrowserBlock(BlockRule.FILE))

    async def allows(self, url: str) -> bool:
        """Return whether `url` names a file inside the roots, as written and once resolved.

        Args:
            url: A file URL, already canonicalised by Chromium.

        Returns:
            True only when the lexical rule passes, the resolved path is inside a resolved root,
            and it is not a file with a second hard link; False when any fails, or the path
            cannot be resolved or examined in time.
        """
        path = file_url_path(url)
        if path is None or file_url_escapes(url, self._roots):
            return False
        try:
            # External await: a readlink walk and one stat on the local disk, bounded.
            async with asyncio.timeout(RESOLVE_TIMEOUT_S):
                real = await asyncio.to_thread(_single_named, path)
        except (TimeoutError, OSError):
            return False  # A path that cannot be resolved cannot be shown to stay inside.
        return real is not None and is_within(real, self._resolved_roots)


def _single_named(path: PurePath) -> Path | None:
    """Resolve `path`'s symlinks; None when it is a file that another hard link also names.

    A missing file resolves as its path: there is nothing to read, and Chromium shows its own
    not-found page. Any other failure to examine it raises OSError, which refuses the request.
    """
    real = Path(os.path.realpath(path))
    try:
        info = real.stat()
    except FileNotFoundError:
        return real
    # A directory's link count is its subdirectories plus two; only a file's means another name.
    if not stat.S_ISDIR(info.st_mode) and info.st_nlink > 1:
        return None
    return real


def _resolve_all(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Resolve every root's symlinks, so a resolved file is compared with resolved roots."""
    return tuple(Path(os.path.realpath(root)) for root in roots)
