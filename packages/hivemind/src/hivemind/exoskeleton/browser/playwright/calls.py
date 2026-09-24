"""Make one Playwright call for the browser fast path: refused once closed, bounded, errors mapped.

Every operation `PlaywrightBrowser` performs (a click, a read, a checkpoint's cookie read) is one
or more calls into Playwright, and each must fail the same way: a closed connection refuses the
call before anything is sent; the call is bounded twice, by the timeout Playwright is given and by
an asyncio timeout a little longer, so a browser that stops answering is an error and never a hang
(codingrules section 11); and every Playwright error leaves as a `PeripheralError` naming the
operation, with a detail cut from Playwright's first line: the API name dropped, URLs masked, and
the whole detail withheld if it quotes text that was typed into a field (it may be a password).
A Playwright timeout while waiting for an element becomes an `ElementNotFoundError` instead, the
failure a bee corrects by naming a different element. `PlaywrightCalls` is that rule, written once;
`close_quietly` is its counterpart for cleanup, which logs a failure instead of raising it so it
never masks the error already on its way out.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.playwright`. Used by `.page` and `.storage`. Calls into
    Playwright's error types, `browser.excerpts`, `browser.targets` and
    `hivemind.exoskeleton.errors` only.

Key invariants:
    - After `close()`, `run` raises without calling Playwright (or even creating the coroutine).
    - An error detail is at most MAX_DETAIL_CHARS, holds no URL and never contains `typed`.

See Also:
    - hivemind.exoskeleton.browser.excerpts for `detail`.
    - hivemind.exoskeleton.errors for PeripheralError and ElementNotFoundError.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from hivemind.common.logging import get_logger
from hivemind.exoskeleton.browser.excerpts import detail
from hivemind.exoskeleton.browser.targets import PERIPHERAL
from hivemind.exoskeleton.errors import ElementNotFoundError, PeripheralError
from waggle.messages.capping import ElementTarget

CALL_MARGIN_S = 5.0  # How much longer a call's asyncio bound is than Playwright's own timeout.
WITHHELD = "the details are withheld because they may quote the typed text"
# The "Locator.click: Error: " Playwright puts before a message; the operation already says it.
_API_PREFIX = re.compile(r"^(?:[A-Za-z]+\.[A-Za-z_]+: )?(?:Error: )?")

__all__ = ["CALL_MARGIN_S", "WITHHELD", "Closable", "PlaywrightCalls", "close_quietly", "reason"]

log = get_logger(__name__)


class Closable(Protocol):
    """Anything Playwright lets a caller close: a tab, the CDP connection."""

    async def close(self) -> None:
        """Close it."""
        ...


class PlaywrightCalls:
    """Run Playwright calls for one browser connection until it is closed.

    Owns one piece of mutable state (codingrules 8.5): whether the connection is closed.
    """

    def __init__(self, limit_s: float) -> None:
        """Build the runner.

        Args:
            limit_s: The asyncio bound on every call, in seconds: the longest Playwright timeout
                any call is given plus CALL_MARGIN_S.
        """
        self._limit_s = limit_s
        self._closed = False

    def close(self) -> bool:
        """Refuse every later call.

        Returns:
            True the first time, False when already closed.
        """
        was_open = not self._closed
        self._closed = True
        return was_open

    def check_open(self, operation: str) -> None:
        """Raise when the connection is closed.

        Args:
            operation: What was about to happen, for the error.

        Raises:
            PeripheralError: `close` has run.
        """
        if self._closed:
            raise PeripheralError(PERIPHERAL, operation, "the browser connection is closed")

    async def run[ResultT](
        self,
        operation: str,
        call: Callable[[], Awaitable[ResultT]],
        *,
        typed: str = "",
        missing: ElementTarget | None = None,
    ) -> ResultT:
        """Make one bounded Playwright call.

        Args:
            operation: What the call does, for any error ("click", "read the title").
            call: Makes the call; invoked only once the connection is known to be open.
            typed: Text the call types into a field, kept out of every error.
            missing: The element the call waits for; a timeout then means it was not found.

        Returns:
            Whatever the call returns.

        Raises:
            ElementNotFoundError: `missing` was given and Playwright timed out waiting.
            PeripheralError: The connection is closed, Playwright failed or timed out, or the
                call outlived its asyncio bound.
        """
        self.check_open(operation)
        try:
            async with asyncio.timeout(self._limit_s):
                return await call()
        except PlaywrightTimeoutError as error:
            if missing is not None:
                raise ElementNotFoundError(missing.describe(), operation) from error
            raise PeripheralError(
                PERIPHERAL, operation, "the page did not respond in time"
            ) from error
        except PlaywrightError as error:
            raise PeripheralError(PERIPHERAL, operation, reason(error, typed)) from error
        except TimeoutError as error:
            late = f"the browser did not answer within {self._limit_s}s"
            raise PeripheralError(PERIPHERAL, operation, late) from error


def reason(error: PlaywrightError, typed: str = "") -> str:
    """Reduce a Playwright error to a short detail that never repeats typed text.

    Args:
        error: What Playwright raised.
        typed: Text the failed call typed; the detail is withheld whole if the message holds it.

    Returns:
        The message's first line without Playwright's API prefix, URLs masked, bounded; WITHHELD
        when it quotes `typed`; the error's class name when nothing else is left.

    Example:
        >>> reason(PlaywrightError("Page.goto: net::ERR_FILE_NOT_FOUND at file:///x"))
        'net::ERR_FILE_NOT_FOUND at <url>'
    """
    # Checked against the whole message, call log included: the typed text may be a password.
    if typed and typed in error.message:
        return WITHHELD
    first = _API_PREFIX.sub("", error.message.split("\n", 1)[0])
    return detail(first) or type(error).__name__


async def close_quietly(handle: Closable | Playwright) -> None:
    """Close a tab or a connection, or stop the driver, logging a failure instead of raising it.

    For cleanup that must not mask the error already on its way out, and for a close whose target
    may already be gone (a browser the lease stopped first). Bounded by CALL_MARGIN_S.

    Args:
        handle: A tab or the CDP connection (closed), or Playwright's driver (stopped).
    """
    try:
        # A local operation: milliseconds, bounded so a wedged driver cannot hang a close.
        async with asyncio.timeout(CALL_MARGIN_S):
            await (handle.stop() if isinstance(handle, Playwright) else handle.close())
    except (PlaywrightError, TimeoutError, OSError) as error:
        log.debug("exoskeleton.browser_close_failed", reason=type(error).__name__)
