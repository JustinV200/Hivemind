"""Start a background job a Warden owns: work that must not run inside a tick.

A tick drains the inbox and acts; it never awaits a model, a sandbox or a reply (codingrules
section 8.8: nothing awaits inside a tick). Scaffolding a tool, proving it in a sandbox and
shipping a blob up the wire all take longer than a tick and wait for answers that later ticks
deliver, so they run as tasks. `start_job` is the one way the Warden's tool and Basket handlers
start one: the task is kept in `warden._tool_jobs` so `Warden.stop` can cancel and reap it, and a
job that ends with an unexpected exception is logged rather than left for the garbage collector
to complain about (codingrules section 11: every task has an owner).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Called by `hivemind.wardens.ticks.tools` and `.basket.retain`.
    Calls into `hivemind.common.logging` only.

Key invariants:
    - Every task started here is in `warden._tool_jobs` until it ends, and `Warden.stop` reaps
      the set before it returns.
    - A finished job removes itself; a cancelled one is not an error.

See Also:
    - .claude/codingrules.md section 11 for the owned-task rule.
    - hivemind.wardens.warden.Warden.stop for where the set is reaped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from hivemind.common.logging import get_logger

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["start_job"]

log = get_logger(__name__)


def start_job(warden: Warden, work: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """Run `work` as a task `warden` owns; it is reaped when the Warden stops.

    Args:
        warden: The owning Warden; its `_tool_jobs` set holds the task.
        work: The coroutine to run; it should catch the errors it can answer for itself.

    Returns:
        The task, for a caller that wants to await it (a test does; the tick never does).
    """
    task = asyncio.ensure_future(work)
    warden._tool_jobs.add(task)
    task.add_done_callback(lambda finished: _forget(warden, finished))
    return task


def _forget(warden: Warden, task: asyncio.Task[None]) -> None:
    """Drop a finished job from its owner, logging an unexpected failure once."""
    warden._tool_jobs.discard(task)
    # A cancelled job is the normal end of `stop()`; anything else that escaped is a bug to see.
    if not task.cancelled() and task.exception() is not None:
        log.error("wardens.job_failed", error=repr(task.exception()))
