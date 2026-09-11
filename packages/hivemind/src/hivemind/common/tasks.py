"""Reap an asyncio task: cancel it if it is still running, then await its one remaining turn.

Every tick loop in the Hive races a throwaway waiter (``_stop.wait()``, a receive, a deadline)
against its other sources and discards the losers. ``Task.cancel`` only schedules the
cancellation; the task still needs one turn of the event loop to finish, and when the tick that
cancelled it was the loop's last, the event loop closes first and asyncio reports the task
destroyed while pending. Awaiting the cancelled task is that one turn, so :func:`reap` is how
every loop discards a waiter it no longer needs (codingrules section 11). :func:`reap_all` is the
same operation over a whole collection, for a `stop()`/`aclose()` that owns several persistent
waiters at once (receive tasks, a heartbeat deadline) and must not return until every one of them
is gone. :func:`reaping` is the same guarantee for one tick's own throwaway waiter, raced with
`asyncio.wait` inside the block: it reaps on the way out whether the block finished normally or
the tick itself was cancelled from outside, which a bare call after the race misses entirely (a
cancellation raised out of the `asyncio.wait` line never reaches the line after it).

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal). Called by the Queen, every Warden, every
    Worker runtime and `hivemind.wardens.spawn` from inside a `_tick` or a `stop()`/`aclose()`.

Key invariants:
    - ``reap`` never raises ``CancelledError`` for the task it reaps; a task that finished with
      any other exception re-raises it, since a real failure must never be swallowed here.
    - A task that is already done is left as it is: reaping is idempotent.
    - ``reap_all`` reaps every task in order, one at a time: a real failure on an earlier task
      still stops it before reaching the rest, the same way an unhandled exception would anywhere
      else -- callers that need every one attempted regardless call ``reap`` themselves in a loop.
    - ``reaping`` reaps its own task in a ``finally``, so it runs even when the ``with`` block
      raises -- ``CancelledError`` included -- and always re-raises whatever the block raised.

See Also:
    - .claude/codingrules.md section 11 for the async rules every loop follows.
    - waggle.loop for TickLoop, whose subclasses are the callers.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Iterable
from typing import Any

__all__ = ["reap", "reap_all", "reaping"]


async def reap(task: asyncio.Future[Any]) -> None:
    """Cancel `task` if it is still pending, then await it so it is never destroyed pending.

    Args:
        task: The waiter to discard (``Future`` is invariant, hence ``Any``); typically an
            ``asyncio.Task`` this loop created for one
            ``asyncio.wait`` race and no longer needs.

    Raises:
        Exception: Whatever `task` itself raised, other than its own cancellation.
    """
    if not task.done():
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def reap_all(tasks: Iterable[asyncio.Future[Any]]) -> None:
    """Reap every task in `tasks`, one at a time, in iteration order.

    Args:
        tasks: The waiters to discard; each is cancelled if still pending and then awaited,
            exactly as a bare `reap` call would.

    Raises:
        Exception: Whatever the first non-cancellation failure among `tasks` raised; later tasks
            in the iterable are never reached once that happens.
    """
    for task in tasks:
        await reap(task)


@contextlib.asynccontextmanager
async def reaping(task: asyncio.Future[Any]) -> AsyncIterator[asyncio.Future[Any]]:
    """Yield `task`, then reap it in a `finally`: on a normal return, a raise, or a cancellation.

    A tick that races `task` (typically a throwaway `_stop.wait()`) with `asyncio.wait` inside the
    block still reaps it when the tick itself is cancelled from outside mid-race -- a plain
    ``await reap(task)`` placed after the `asyncio.wait` line is never reached in that case, since
    the cancellation propagates out of that line directly (codingrules section 11; this dispatch's
    own rule 1).

    Args:
        task: The waiter to discard once the block ends.

    Yields:
        `task`, unchanged, for the block to reference (typically to race it with `asyncio.wait`).
    """
    try:
        yield task
    finally:
        await reap(task)
