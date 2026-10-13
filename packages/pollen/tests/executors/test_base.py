"""Test the seam every executor shares: the task scope, the payload checks and the error frame.

The Pollen Packet (the thin gateway program on a device the Hive borrows) runs long work (an exec,
a file read streaming back, a release) as tasks owned by the packet, never by a connection, so a
dropped link stops the streaming and not the process. The ``TaskScope`` is that owner: it logs a
task's failure by its type and lets the others run, and it cancels what is left when the packet
stops. Beside it sit the small helpers every handler uses: reading a payload as the class it
expects, finding the id an answer correlates to (a file transfer's is its first chunk's), and
turning a refusal into the ``control.error`` the sender is told. These tests run the scope on a
real event loop and build the frames from real wire models.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Tests pollen.executors.base.

Key invariants:
    - A failing task is logged by the name of its type and never by its text, which may name a path.
    - Whatever a test spawns has ended when its scope has.

See Also:
    - docs/waggle/gateway-contract.md sections 9 to 11, 15.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import ClassVar

import pytest
from states import frame_from, sample_state

from pollen.errors import InvalidRequestError, PathRefusedError, PollenError
from pollen.executors.base import (
    EXEC,
    Need,
    Prepared,
    TaskScope,
    error_payload,
    payload_of,
    request_id_of,
)
from waggle.clock import FakeClock
from waggle.ids import new_lease_id, new_message_id
from waggle.messages.control.protocol import MAX_ERROR_MESSAGE_CHARS
from waggle.messages.session import SessionClose, SessionPutFile

CLOCK = FakeClock()
LIMIT_S = 10.0  # A bound on tasks that should finish at once, so a hang fails the test.


async def _forever() -> None:
    """Wait until cancelled."""
    await asyncio.Event().wait()


async def test_spawning_before_the_scope_is_open_is_refused_and_leaves_no_coroutine() -> None:
    scope = TaskScope()
    work = _forever()
    with pytest.raises(RuntimeError, match="not open"):
        scope.spawn(work, "early")
    assert inspect.getcoroutinestate(work) == inspect.CORO_CLOSED


async def test_spawning_after_the_scope_has_ended_is_refused() -> None:
    async with TaskScope() as scope:
        pass
    with pytest.raises(RuntimeError, match="not open"):
        scope.spawn(_forever(), "late")


async def test_leaving_the_scope_cancels_what_is_still_running() -> None:
    async with TaskScope() as scope:
        task = scope.spawn(_forever(), "waiting")
        await asyncio.sleep(0)  # Let it start, so the cancel arrives in the middle of its wait.
    assert task.cancelled()


async def test_an_error_in_the_body_is_raised_as_it_was_not_wrapped_in_a_group() -> None:
    with pytest.raises(ValueError, match="body failed"):
        async with TaskScope() as scope:
            task = scope.spawn(_forever(), "waiting")
            raise ValueError("body failed")
    assert task.cancelled()


async def test_a_task_that_fails_is_logged_by_type_and_ends_alone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="pollen.executors.base")
    release = asyncio.Event()

    async def survivor() -> None:
        await release.wait()

    async def failing() -> None:
        raise ValueError("secret detail about /etc/shadow")

    async with TaskScope() as scope:
        keeper = scope.spawn(survivor(), "survivor")
        broken = scope.spawn(failing(), "failing")
        await asyncio.wait_for(broken, timeout=LIMIT_S)  # It ended without raising: contained.
        assert not keeper.done()
        release.set()
        await asyncio.wait_for(keeper, timeout=LIMIT_S)
    assert "task=failing error=ValueError" in caplog.text
    assert "shadow" not in caplog.text and "secret" not in caplog.text


def test_a_prepared_request_needs_nothing_by_default() -> None:
    async def work() -> None:
        return None

    prepared = Prepared(work)
    assert (prepared.needs, prepared.scratch_root) == ((), None)
    assert Need(EXEC, "git") != Need(EXEC, "make")


def test_a_payload_of_the_expected_class_is_returned_as_it_is() -> None:
    state = sample_state(CLOCK)
    payload = SessionClose(lease_id=new_lease_id(CLOCK), reason="The test is over.")
    assert payload_of(frame_from(state, payload, CLOCK), SessionClose) is payload


def test_a_payload_of_another_class_is_refused_by_id_only() -> None:
    state = sample_state(CLOCK)
    payload = SessionClose(lease_id=new_lease_id(CLOCK), reason="Private words.")
    frame = frame_from(state, payload, CLOCK)
    with pytest.raises(InvalidRequestError) as raised:
        payload_of(frame, SessionPutFile)
    assert frame.id in str(raised.value) and "Private words" not in str(raised.value)


def test_a_request_is_answered_under_its_own_id() -> None:
    state = sample_state(CLOCK)
    payload = SessionClose(lease_id=new_lease_id(CLOCK), reason="The test is over.")
    frame = frame_from(state, payload, CLOCK)
    assert request_id_of(frame) == frame.id


def test_a_put_file_chunk_is_answered_under_its_transfer() -> None:
    state = sample_state(CLOCK)
    transfer = new_message_id(CLOCK)
    chunk = SessionPutFile(
        lease_id=new_lease_id(CLOCK),
        transfer_id=transfer,
        total_bytes=10,
        path="out.bin",
        chunk=b"abc",
        offset=3,
        final=False,
        sha256=None,
        task_id=None,
        worker_id=None,
    )
    frame = frame_from(state, chunk, CLOCK)
    assert request_id_of(frame) == transfer != frame.id  # Every chunk echoes the first one's id.


class _TransientError(PollenError):
    """A refusal a sender may retry."""

    code: ClassVar[str] = "pollen.transient"
    is_retryable: ClassVar[bool] = True


def test_a_refusal_becomes_the_error_frame_with_its_code_and_retryability() -> None:
    refused = error_payload(PathRefusedError("Request x names a path outside scratch."), "kind.x")
    assert (refused.code, refused.failed_kind, refused.is_retryable) == (
        "pollen.path_refused",
        "kind.x",
        False,
    )
    assert refused.message == "Request x names a path outside scratch."
    assert error_payload(_TransientError("Try again."), None).is_retryable is True


def test_a_refusal_with_no_text_says_its_code_and_a_long_one_is_cut() -> None:
    assert error_payload(PollenError(), None).message == "pollen.error"
    long_text = "x" * (MAX_ERROR_MESSAGE_CHARS + 500)
    assert len(error_payload(PathRefusedError(long_text), None).message) == MAX_ERROR_MESSAGE_CHARS
