"""Tests for hivemind.entrance.auth.session.book: opening, judging and ending sessions.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/session/book.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.session.book for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.entrance import (
    LOOPBACK,
    REMOTE,
    admitted_program,
    auth_rig,
    make_device,
    program_login,
)

from hivemind.entrance.auth import BindingKind, EndReason
from hivemind.entrance.auth.session import SessionGrant, token_hash
from hivemind.entrance.enrol import DeviceStatus, LockReason, lock, revoke
from hivemind.entrance.store import EntranceStore


async def _ended(store: EntranceStore, hashed: str) -> EndReason | None:
    """Return why the durable session with ``hashed`` ended (None while open)."""
    session = await store.sessions.get(hashed)
    assert session is not None
    return session.end_reason


async def test_open_returns_the_token_once_and_keeps_only_its_hash() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)

    opened = await program_login(auth, device, signer)

    stored = await auth.store.sessions.get(opened.session.token_hash)
    assert stored == opened.session
    assert opened.session.token_hash == token_hash(opened.token)
    assert opened.token not in repr(opened)
    assert opened.session.expires_at == auth.clock.now() + timedelta(hours=12)


async def test_a_session_never_outlives_its_devices_approval() -> None:
    auth = await auth_rig()
    device, _ = await admitted_program(auth.enrolment)
    soon = device.model_copy(update={"expires_at": auth.clock.now() + timedelta(hours=1)})
    grant = SessionGrant(soon, BindingKind.ED25519, soon.public_key or "", LOOPBACK, None)

    opened = await auth.book.open(grant)

    assert opened.session.expires_at == auth.clock.now() + timedelta(hours=1)


async def test_the_consoles_session_is_kept_in_memory_only() -> None:
    auth = await auth_rig()
    console = make_device(
        auth.clock, DeviceStatus.APPROVED, loopback_bound=True, interactive=True, expires_at=None
    )
    grant = SessionGrant(console, BindingKind.ED25519, console.public_key or "", LOOPBACK, None)

    opened = await auth.book.open(grant)

    assert opened.session.volatile
    assert await auth.store.sessions.get(opened.session.token_hash) is None
    assert await auth.book.tables.get(opened.session.token_hash) == opened.session


async def test_live_ends_an_idle_session_and_one_past_its_absolute_expiry() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    idle = await program_login(auth, device, signer)
    busy = await program_login(auth, device, signer)
    auth.clock.advance(29 * 60)
    await auth.book.tables.touch(busy.session.token_hash, auth.clock.now())
    auth.clock.advance(2 * 60)

    idle_alive = await auth.book.live(idle.session.token_hash, auth.clock.now())
    # Busy all along: a request every 29 minutes, until the twelve hours are up.
    while auth.clock.now() < busy.session.expires_at:
        await auth.book.tables.touch(busy.session.token_hash, auth.clock.now())
        auth.clock.advance(29 * 60)
    busy_alive = await auth.book.live(busy.session.token_hash, auth.clock.now())

    assert (idle_alive, busy_alive) == (None, None)
    assert await _ended(auth.store, idle.session.token_hash) is EndReason.IDLE
    assert await _ended(auth.store, busy.session.token_hash) is EndReason.EXPIRED


async def test_live_ends_a_session_whose_device_was_locked_behind_its_back() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    # Locked through the recording offboarder, so only live() can notice.
    await lock(auth.enrolment.deps, device.id, "system", LockReason.REMOTE_LOCK)

    alive = await auth.book.live(opened.session.token_hash, auth.clock.now())

    assert alive is None
    assert await _ended(auth.store, opened.session.token_hash) is EndReason.LOCKED


async def test_revoking_a_device_ends_its_sessions_through_the_offboarder() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    sessions = [await program_login(auth, device, signer) for _ in range(2)]

    await revoke(auth.deps.enrolment, device.id, "human", cancel_goals=False)

    for opened in sessions:
        assert await _ended(auth.store, opened.session.token_hash) is EndReason.REVOKED


async def test_end_remote_ends_only_remote_sessions() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    local = await program_login(auth, device, signer, LOOPBACK)
    remote = await program_login(auth, device, signer, REMOTE)

    ended = await auth.book.end_remote()

    assert ended == 1
    assert await _ended(auth.store, remote.session.token_hash) is EndReason.REDUCED
    assert await _ended(auth.store, local.session.token_hash) is None


async def test_revalidate_ends_what_a_restart_must_not_revive() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    kept, other = await admitted_program(auth.enrolment)
    doomed = await program_login(auth, device, signer)
    alive = await program_login(auth, kept, other)
    await revoke(auth.enrolment.deps, device.id, "human", cancel_goals=False)

    ended = await auth.book.revalidate()

    assert ended == 1
    assert await _ended(auth.store, doomed.session.token_hash) is EndReason.REVOKED
    assert [open_.token_hash for open_ in await auth.book.tables.list_open()] == [
        alive.session.token_hash
    ]


async def test_logout_and_step_up_act_on_one_session() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)

    stepped = await auth.book.mark_stepped_up(opened.session, auth.clock.now())
    logged_out = await auth.book.logout(opened.session)

    assert stepped is not None
    assert stepped.stepped_up_until == auth.clock.now() + timedelta(minutes=5)
    assert logged_out
    assert await _ended(auth.store, opened.session.token_hash) is EndReason.LOGOUT
    assert await auth.book.mark_stepped_up(opened.session, auth.clock.now()) is None
