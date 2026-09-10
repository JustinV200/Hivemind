"""Tests for hivemind.common.tasks.

Mirrors src/hivemind/common/tasks.py (codingrules section 3: tests/unit mirrors src/ one-to-one).
"""

from __future__ import annotations

import asyncio

import pytest

from hivemind.common.tasks import reap


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
