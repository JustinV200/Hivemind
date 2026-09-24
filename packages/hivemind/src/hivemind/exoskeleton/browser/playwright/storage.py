"""Keep what a browser checkpoint covers: every cookie, and every loaded origin's local storage.

A browser checkpoint (the Capping gate's undo point for a GUI action, ADR-0032) is the page's URL
plus a `BrowserState`: the browser's cookies and the local storage of every origin the page has
loaded. `CheckpointStorage` tracks those origins as the page's frames load documents, captures the
state, and replaces it on restore: every cookie cleared and the checkpoint's put back, and every
origin known then or now given exactly the checkpoint's items, which is what removes anything
created since. An origin's storage can only be reached from a document of that origin. The page's
own origin is read in place; any other is reached in a short-lived blank tab, so a checkpoint never
moves the page. For http and https every request that tab makes is answered with an empty page, so
no server ever sees it; for file://, whose pages share one storage area in Chromium, the tab opens
the listing of a directory the page loaded a file from, which runs none of the site's scripts.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Owned by `.page.PlaywrightBrowser`, which calls it
    from `checkpoint` and `restore`. Calls into Playwright, `.calls`, `browser.state` and
    `hivemind.exoskeleton.errors` only.

Key invariants:
    - `capture` never navigates the page; `replace` leaves it where it was (restore reloads it).
    - Every blank tab this opens is closed again, whether the script in it succeeded or not.
    - A cookie's value and a stored item never reach a log or an error.

See Also:
    - hivemind.exoskeleton.browser.state for BrowserState and storage_origin.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, TypedDict

from playwright.async_api import BrowserContext, Cookie, Page, Route
from playwright.async_api import Frame as PageFrame

from hivemind.exoskeleton.browser.playwright.calls import PlaywrightCalls, close_quietly
from hivemind.exoskeleton.browser.state import (
    FILE_ORIGIN,
    BrowserState,
    StoredCookie,
    storage_origin,
)
from hivemind.exoskeleton.browser.targets import PERIPHERAL
from hivemind.exoskeleton.errors import PeripheralError

_REACH = "reach local storage"  # The operation every storage call reports as.
_BLANK_PAGE = "<!doctype html><title></title>"  # What a blank tab's every request is answered with.
_READ_STORAGE_JS = """() => {
  const items = {};
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index);
    items[key] = localStorage.getItem(key);
  }
  return items;
}"""
_REPLACE_STORAGE_JS = """items => {
  localStorage.clear();
  for (const [key, value] of Object.entries(items)) localStorage.setItem(key, value);
}"""
# Playwright's add_cookies takes this shape (its SetCookieParam, which it does not export).
_CookieParam = TypedDict(  # noqa: UP013 -- class syntax would trip N815 on each camelCase key.
    "_CookieParam",
    {
        "name": str,
        "value": str,
        "url": str | None,
        "domain": str | None,
        "path": str | None,
        "expires": float | None,
        "httpOnly": bool | None,
        "secure": bool | None,
        "sameSite": Literal["Lax", "None", "Strict"] | None,
        "partitionKey": str | None,
    },
    total=False,
)

__all__ = ["CheckpointStorage"]


class CheckpointStorage:
    """Capture and replace one browser's cookies and its page's origins' local storage.

    Owns mutable state (codingrules 8.5): the origins the page has loaded and the directory of the
    last file:// page, both updated by the page's own navigation events.
    """

    def __init__(
        self, page: Page, context: BrowserContext, calls: PlaywrightCalls, navigation_ms: float
    ) -> None:
        """Start tracking `page`'s origins.

        Args:
            page: The page a Browser drives.
            context: Its browser context, where cookies and blank tabs live.
            calls: The connection's call runner, shared with the Browser.
            navigation_ms: Playwright's timeout for a blank tab's load, in milliseconds.
        """
        self._page = page
        self._context = context
        self._calls = calls
        self._navigation_ms = navigation_ms
        self._origins: set[str] = set()
        self._file_directory: str | None = None
        self._note_url(page.url)
        page.on("framenavigated", self._note_frame)

    async def capture(self) -> BrowserState:
        """Read every cookie and every known origin's items.

        Returns:
            The state a checkpoint carries.

        Raises:
            PeripheralError: The browser could not be read.
        """
        cookies = await self._calls.run("checkpoint", self._context.cookies)
        storage: dict[str, dict[str, str]] = {}
        # Every origin's items, read where the page is or in a blank tab.
        for origin in sorted(self._known_origins()):
            storage[origin] = _items(await self._in_origin(origin, _READ_STORAGE_JS, None))
        return BrowserState(cookies=tuple(map(_stored, cookies)), local_storage=storage)

    async def replace(self, state: BrowserState) -> None:
        """Make the cookies and every known origin's items exactly `state`'s.

        Args:
            state: What `capture` returned earlier on this browser.

        Raises:
            PeripheralError: The browser refused a cookie or a storage write.
        """
        # Clearing first is what removes every cookie created since the checkpoint.
        await self._calls.run("restore", self._context.clear_cookies)
        if state.cookies:
            params = _cookie_params(state.cookies)
            await self._calls.run("restore", lambda: self._context.add_cookies(params))
        # Every origin known now or then: one the checkpoint never saw ends up empty.
        for origin in sorted(self._known_origins() | set(state.local_storage)):
            items = state.local_storage.get(origin, {})
            await self._in_origin(origin, _REPLACE_STORAGE_JS, items)

    async def _in_origin(self, origin: str, script: str, items: Mapping[str, str] | None) -> object:
        """Run a storage script in a document of `origin`: the page's own, else a blank tab's."""
        if origin == storage_origin(self._page.url):
            return await self._calls.run(_REACH, lambda: self._page.evaluate(script, items))
        document = self._blank_document(origin)
        tab = await self._calls.run(_REACH, self._context.new_page)
        try:
            if origin != FILE_ORIGIN:
                # Every request the tab makes is answered here, so no server ever sees it.
                await self._calls.run(_REACH, lambda: tab.route("**/*", _answer_blank))
            # A blank document or a directory listing: loads in milliseconds.
            await self._calls.run(_REACH, lambda: tab.goto(document, timeout=self._navigation_ms))
            return await self._calls.run(_REACH, lambda: tab.evaluate(script, items))
        finally:
            await close_quietly(tab)

    def _blank_document(self, origin: str) -> str:
        """Return a URL whose document has `origin` and runs none of the site's scripts."""
        if origin != FILE_ORIGIN:
            return f"{origin}/"
        if self._file_directory is None:
            raise PeripheralError(PERIPHERAL, _REACH, "no file:// page was loaded to reach it by")
        return self._file_directory

    def _known_origins(self) -> set[str]:
        """Return every origin a checkpoint covers: all the page loaded, its current one too."""
        here = storage_origin(self._page.url)
        return self._origins | ({here} if here is not None else set())

    def _note_frame(self, frame: PageFrame) -> None:
        """Remember the origin of every document a frame of the page loads (an event handler)."""
        self._note_url(frame.url)

    def _note_url(self, url: str) -> None:
        """Remember `url`'s origin, and a file:// page's directory to reach file storage by."""
        origin = storage_origin(url)
        if origin is None:
            return
        self._origins.add(origin)
        if origin == FILE_ORIGIN:
            self._file_directory = url.split("#", 1)[0].split("?", 1)[0].rsplit("/", 1)[0] + "/"


def _items(value: object) -> dict[str, str]:
    """Return a storage read's result as items, or fail the checkpoint when it is not text."""
    if isinstance(value, dict):
        items = {key: item for key, item in value.items() if isinstance(key, str)}
        if len(items) == len(value) and all(isinstance(item, str) for item in items.values()):
            return {key: str(item) for key, item in items.items()}
    raise PeripheralError(PERIPHERAL, "checkpoint", "local storage did not read back as text")


def _stored(cookie: Cookie) -> StoredCookie:
    """Keep one cookie exactly as the browser reported it."""
    return StoredCookie(
        name=cookie.get("name", ""),
        value=cookie.get("value", ""),
        domain=cookie.get("domain", ""),
        path=cookie.get("path", "/"),
        expires=cookie.get("expires", -1),
        http_only=cookie.get("httpOnly", False),
        secure=cookie.get("secure", False),
        same_site=cookie.get("sameSite", "Lax"),
        partition_key=cookie.get("partitionKey"),
    )


def _cookie_params(cookies: Iterable[StoredCookie]) -> list[_CookieParam]:
    """Turn kept cookies back into what add_cookies takes."""
    params: list[_CookieParam] = []
    for cookie in cookies:
        param: _CookieParam = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "expires": cookie.expires,
            "httpOnly": cookie.http_only,
            "secure": cookie.secure,
            "sameSite": cookie.same_site,
        }
        # Playwright refuses a null partition key, so an unpartitioned cookie carries none.
        if cookie.partition_key is not None:
            param["partitionKey"] = cookie.partition_key
        params.append(param)
    return params


async def _answer_blank(route: Route) -> None:
    """Answer one request of a blank tab with an empty page of the origin it asked for."""
    await route.fulfill(status=200, content_type="text/html", body=_BLANK_PAGE)
