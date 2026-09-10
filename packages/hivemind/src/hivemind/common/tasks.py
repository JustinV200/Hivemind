"""Reap an asyncio task: cancel it if it is still running, then await its one remaining turn.

Every tick loop in the Hive races a throwaway waiter (``_stop.wait()``, a receive, a deadline)
against its other sources and discards the losers. ``Task.cancel`` only schedules the
cancellation; the task still needs one turn of the event loop to finish, and when the tick that
cancelled it was the loop's last, the event loop closes first and asyncio reports the task
destroyed while pending. Awaiting the cancelled task is that one turn, so :func:`reap` is how
every loop discards a waiter it no longer needs (codingrules section 11).

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal). Called by the Queen, every Warden and every
    Worker runtime from inside their ``_tick``.

Key invariants:
    - ``reap`` never raises ``CancelledError`` for the task it reaps; a task that finished with
      any other exception re-raises it, since a real failure must never be swallowed here.
    - A task that is already done is left as it is: reaping is idempotent.

See Also:
    - .claude/codingrules.md section 11 for the async rules every loop follows.
    - waggle.loop for TickLoop, whose subclasses are the callers.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

__all__ = ["reap"]


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
