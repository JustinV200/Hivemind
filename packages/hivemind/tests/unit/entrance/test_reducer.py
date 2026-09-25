"""Tests for hivemind.entrance.reducer: drop to loopback, persist it, reopen only from loopback.

Roadmap 10.5e's third named test is the app's (the Reducer closes a live WebSocket within a
second); its Reducer half is here: every remote session is ended and both seams are called, the
socket closer even when stopping the listener fails. A restart comes back reduced.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reducer.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.reducer for the module under test.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from builders.entrance import (
    LOOPBACK,
    REMOTE,
    AuthRig,
    RecordingListener,
    RecordingStreams,
    admitted_console,
    admitted_program,
    auth_rig,
    make_session,
    program_login,
    session_book,
    sqlite_enrolment,
)

from hivemind.common.sqlite import connect
from hivemind.entrance.auth import AuthenticatedSession, EndReason, Listener
from hivemind.entrance.errors import InvalidModeTransitionError, ReopenRefusedError
from hivemind.entrance.reducer import (
    MODE_TRANSITIONS,
    EntranceMode,
    EntranceReducer,
    ReduceReason,
    ReducerSeams,
    mode_trail_kind,
)
from hivemind.entrance.store import SqliteEntranceStore
from hivemind.guard import CapabilitySet


def _reducer(auth: AuthRig, listener: RecordingListener | None = None) -> EntranceReducer:
    """A reducer over the rig, with recording seams."""
    seams = ReducerSeams(listener or RecordingListener(), RecordingStreams())
    return EntranceReducer(auth.deps.records, auth.book, seams)


async def _console_session(
    auth: AuthRig, listener: Listener, stepped_up: bool
) -> AuthenticatedSession:
    """An authenticated console session on ``listener``, stepped up or not."""
    console, _ = await admitted_console(auth)
    session = make_session(console.id, auth.clock, listener=listener)
    return AuthenticatedSession(session, console, CapabilitySet.empty(), stepped_up)


async def test_reducing_ends_every_remote_session_and_calls_both_seams() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    remote = await program_login(auth, device, signer, REMOTE)
    local = await program_login(auth, device, signer, LOOPBACK)
    listener, streams = RecordingListener(), RecordingStreams()
    reducer = EntranceReducer(auth.deps.records, auth.book, ReducerSeams(listener, streams))

    changed = await reducer.reduce(ReduceReason.OPERATOR, "human")

    assert changed
    assert await auth.store.entrance_mode.get() is EntranceMode.REDUCED
    ended = await auth.store.sessions.get(remote.session.token_hash)
    kept = await auth.store.sessions.get(local.session.token_hash)
    assert ended is not None and ended.end_reason is EndReason.REDUCED
    assert kept is not None and kept.is_open
    (event,) = await auth.enrolment.events("guard.reduced")
    assert (event.actor, event.subject_id, event.payload) == (
        "human",
        auth.deps.records.identity.hive_id,
        {"reason": "operator"},
    )
    assert (listener.calls, streams.closes) == (["stop"], 1)


async def test_reducing_twice_records_nothing_more_but_reasserts_the_narrowing() -> None:
    auth = await auth_rig()
    listener = RecordingListener()
    reducer = _reducer(auth, listener)
    await reducer.reduce(ReduceReason.GUARD_ORDER, "system")

    again = await reducer.reduce(ReduceReason.OPERATOR, "human")

    assert not again
    assert len(await auth.enrolment.events("guard.reduced")) == 1
    assert listener.calls == ["stop", "stop"]


async def test_a_failed_listener_stop_still_ends_sessions_and_closes_sockets() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    remote = await program_login(auth, device, signer, REMOTE)
    streams = RecordingStreams()
    reducer = EntranceReducer(
        auth.deps.records, auth.book, ReducerSeams(RecordingListener(fail_stop=True), streams)
    )

    with pytest.raises(OSError, match="would not stop"):
        await reducer.reduce(ReduceReason.LISTENER_FAILED, "system")

    ended = await auth.store.sessions.get(remote.session.token_hash)
    assert ended is not None and ended.end_reason is EndReason.REDUCED
    assert streams.closes == 1
    assert await auth.store.entrance_mode.get() is EntranceMode.REDUCED


async def test_a_restart_comes_back_reduced_and_revives_no_remote_session(tmp_path: Path) -> None:
    db = tmp_path / "hive.sqlite3"
    auth = await auth_rig(await sqlite_enrolment(db))
    device, signer = await admitted_program(auth.enrolment)
    await _reducer(auth).reduce(ReduceReason.OPERATOR, "human")
    # A remote session that outlived the reduction (a crash between persist and end).
    late = await program_login(auth, device, signer, LOOPBACK)
    stray = late.session.model_copy(update={"token_hash": "f" * 64, "listener": Listener.REMOTE})
    await auth.store.sessions.put(stray)
    # A restart: a new store, book and reducer over the same file.
    reopened = await SqliteEntranceStore.create(connect(db), auth.clock)
    records = replace(auth.enrolment.deps.records, store=reopened)
    rig = replace(auth.enrolment, deps=replace(auth.enrolment.deps, records=records))
    restarted = EntranceReducer(
        records, session_book(rig), ReducerSeams(RecordingListener(), RecordingStreams())
    )

    mode = await restarted.start_mode()

    assert mode is EntranceMode.REDUCED
    ended = await reopened.sessions.get(stray.token_hash)
    assert ended is not None and ended.end_reason is EndReason.REDUCED


async def test_reopening_needs_a_stepped_up_session_on_the_loopback_listener() -> None:
    auth = await auth_rig()
    listener = RecordingListener()
    reducer = _reducer(auth, listener)
    await reducer.reduce(ReduceReason.OPERATOR, "human")

    for session in (
        await _console_session(auth, Listener.REMOTE, stepped_up=True),
        await _console_session(auth, Listener.LOOPBACK, stepped_up=False),
    ):
        with pytest.raises(ReopenRefusedError):
            await reducer.reopen(session)
    operator = await _console_session(auth, Listener.LOOPBACK, stepped_up=True)
    reopened = await reducer.reopen(operator)

    assert reopened
    assert await auth.store.entrance_mode.get() is EntranceMode.OPEN
    (event,) = await auth.enrolment.events("guard.reopened")
    assert (event.actor, event.payload) == (operator.device.id, {"listener": "loopback"})
    assert listener.calls == ["stop", "start"]
    assert not await reducer.reopen(operator)
    assert listener.calls == ["stop", "start"]


def test_the_mode_machine_has_both_edges_and_nothing_else() -> None:
    assert set(MODE_TRANSITIONS) == set(EntranceMode)
    assert mode_trail_kind(EntranceMode.OPEN, EntranceMode.REDUCED) == "guard.reduced"
    assert mode_trail_kind(EntranceMode.REDUCED, EntranceMode.OPEN) == "guard.reopened"
    for mode in EntranceMode:
        with pytest.raises(InvalidModeTransitionError):
            mode_trail_kind(mode, mode)
