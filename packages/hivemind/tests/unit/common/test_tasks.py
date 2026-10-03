"""Tests for hivemind.common.tasks.

Mirrors src/hivemind/common/tasks.py (codingrules section 3: tests/unit mirrors src/ one-to-one).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from hivemind.common.tasks import reap, run_to_end


async def test_reap_cancels_a_pending_waiter_and_leaves_it_done() -> None:
    event = asyncio.Event()
    task = asyncio.ensure_future(event.wait())
    await asyncio.sleep(0)  # Let the waiter start blocking on the event.

    await reap(task)

    assert task.done() and task.cancelled()


async def test_reap_leaves_a_finished_task_alone_and_is_idempotent() -> None:
    event = asyncio.Event()
    event.set()
    task = asyncio.ensure_future(event.wait())
    await task

    await reap(task)
    await reap(task)

    assert task.done() and not task.cancelled() and task.result() is True


async def test_reap_re_raises_a_real_failure() -> None:
    async def fail() -> None:
        raise RuntimeError("boom")

    task = asyncio.ensure_future(fail())
    await asyncio.sleep(0)

    with pytest.raises(RuntimeError, match="boom"):
        await reap(task)


async def test_reap_keeps_the_reaping_tasks_own_cancellation() -> None:
    """Cancelling a task that is inside `reap` still cancels it.

    Only the reaped task's own cancellation is swallowed, never the caller's: a Warden reaping a
    waiter every tick would otherwise absorb every cancel sent to it.
    """
    started = asyncio.Event()

    async def reaper() -> None:
        never = asyncio.ensure_future(asyncio.Event().wait())
        started.set()
        # A waiter that swallows its own cancellation slowly, so the outer cancel lands here.
        await reap(_slow_to_cancel(never))

    outer = asyncio.ensure_future(reaper())
    await started.wait()
    await asyncio.sleep(0)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(outer, timeout=2.0)


async def test_reap_discards_every_waiter_of_a_task_already_cancelled_and_cleaning_up() -> None:
    """A cancelled task cleaning up in its `finally` reaps waiter after waiter, to the end.

    A Warden's `stop()`, called on the way out of a cancelled run, is this shape: the cancel was
    delivered before the cleanup began, so it must not be taken for a new one that lands in the
    first `reap`, or everything the cleanup still has to do (the rest of the waiters, the lease,
    the link) is abandoned at that waiter.
    """
    waiters = [asyncio.ensure_future(asyncio.Event().wait()) for _ in range(2)]
    cleaned: list[str] = []

    async def run() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            for waiter in waiters:
                await reap(waiter)
                cleaned.append("reaped")
            cleaned.append("finished")

    task = asyncio.ensure_future(run())
    await asyncio.sleep(0)  # Let it block.

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)  # It still ends cancelled, as it was asked.

    assert cleaned == ["reaped", "reaped", "finished"]
    assert all(waiter.cancelled() for waiter in waiters)


async def test_reap_keeps_a_second_cancel_of_a_task_already_cleaning_up() -> None:
    """A cancel that arrives while a cancelled task is reaping still ends it.

    The first cancel started the cleanup; a second (a caller giving up on a slow stop) is a new
    request and must not be mistaken for the first and swallowed.
    """
    reaping_now = asyncio.Event()
    cleaned: list[str] = []

    async def unwinds_slowly() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.2)  # Cleanup of its own that takes a moment, then it ends.

    slow = asyncio.ensure_future(unwinds_slowly())

    async def run() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            reaping_now.set()
            await reap(slow)
            cleaned.append("finished")  # Never reached: the second cancel ends the cleanup.

    task = asyncio.ensure_future(run())
    await asyncio.sleep(0)  # Let both the slow waiter and the task block.
    task.cancel()
    await reaping_now.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    assert cleaned == []


async def _cancelled_with_teardown(teardown: Callable[[], Awaitable[None]]) -> None:
    """Start a body that runs `teardown` in its `finally`, cancel it, and await the cancellation."""
    started = asyncio.Event()

    async def body() -> None:
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await teardown()

    task = asyncio.ensure_future(body())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)


async def _reap_then_note(finished: list[str]) -> None:
    """A teardown in miniature: reap a waiter, then note that it got past the reap."""
    await reap(asyncio.ensure_future(asyncio.Event().wait()))
    finished.append("past the reap")


async def test_run_to_end_finishes_a_teardown_inside_a_cancelled_task() -> None:
    finished: list[str] = []

    await _cancelled_with_teardown(lambda: run_to_end(_reap_then_note(finished)))

    assert finished == ["past the reap"]


async def test_run_to_end_lets_the_work_end_before_its_caller_is_cancelled() -> None:
    release = asyncio.Event()
    finished: list[str] = []

    async def work() -> None:
        await release.wait()
        finished.append("ended")

    caller = asyncio.ensure_future(run_to_end(work()))
    await asyncio.sleep(0)
    caller.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(caller, timeout=2.0)
    assert finished == ["ended"]


async def test_run_to_end_returns_the_works_result_and_raises_its_failure() -> None:
    async def answer() -> int:
        return 42

    async def fail() -> None:
        raise RuntimeError("boom")

    assert await run_to_end(answer()) == 42
    with pytest.raises(RuntimeError, match="boom"):
        await run_to_end(fail())


def _slow_to_cancel(inner: asyncio.Future[Any]) -> asyncio.Task[None]:
    async def body() -> None:
        try:
            await inner
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)  # Cancellation takes a moment; the outer cancel arrives now.

    return asyncio.ensure_future(body())
