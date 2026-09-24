"""The whole enrolment lifecycle through hivemind.entrance.enrol's public API, end to end.

A real run, not a synthetic one (CLAUDE.md): over in-memory tables and over one real SQLite file
holding the Entrance tables and the Pheromone Trail together, the operator bootstraps the Hive
Stand console (real Argon2id), mints two invites, a program redeems one with a real Ed25519 key and
a browser the other with a passkey through the real ``webauthn`` verification, the console
approves both, the phone is locked, unlocked and revoked with its goals cancelled, the program's
approval lapses and the sweep expires it, and an attacker replaying a spent code is refused. Then
every trail event is checked: the right kinds, in order, and not one carrying an invite code, a
key, a signature, a credential or the password.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/__init__.py, the package face whose flows this drives
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol for the package under test.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the lifecycle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from builders.entrance import (
    ORIGIN,
    Enrolment,
    approval,
    ed25519_proof,
    memory_enrolment,
    mint,
    redeem_browser,
    redeem_program,
    sqlite_enrolment,
)

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.auth import PasswordHasher, SoftPasskey, b64url_decode, b64url_encode
from hivemind.entrance.enrol import (
    ConsoleDeps,
    DeviceStatus,
    EnrolledDevice,
    LockReason,
    MintedInvite,
    Redemption,
    Revocation,
    approve,
    bootstrap_operator,
    expire_due,
    invite_code_hash,
    lock,
    revoke,
    unlock,
)
from hivemind.entrance.errors import EnrolmentRefusedError
from waggle.ids import DeviceId, TaskId
from waggle.signing import Ed25519Signer

_PASSWORD = "correct horse battery staple"  # noqa: S105 -- a test's password, not a credential
_GOAL = TaskId("task_01M221E4C10R4XDPNQNRX85AAA")
_NARROW = ("entrance:answer", "entrance:submit", "observe")  # What the program is approved for.
_S = DeviceStatus


@dataclass(frozen=True, slots=True)
class _Enrolled:
    """One device's way in: its invite, the key it holds, and what redeeming gave it."""

    invite: MintedInvite
    key: Ed25519Signer | SoftPasskey
    redemption: Redemption

    @property
    def device_id(self) -> DeviceId:
        """The device's id."""
        return self.redemption.device_id


@dataclass(frozen=True, slots=True)
class _Cast:
    """One run's devices: the console, the program and the phone."""

    console: EnrolledDevice
    program: _Enrolled
    phone: _Enrolled


async def _rig(kind: str, tmp_path: Path) -> Enrolment:
    """An enrolment rig over in-memory tables, or over one real SQLite file."""
    return memory_enrolment() if kind == "memory" else await sqlite_enrolment(tmp_path / "h.db")


async def _enrol_both(rig: Enrolment) -> _Cast:
    """Bootstrap the console, mint two invites, redeem both, and approve both from the console."""
    identity = rig.deps.records.identity
    console_deps = ConsoleDeps(
        rig.store, MemorySecretStore(), PasswordHasher(), rig.clock, identity
    )
    console = await bootstrap_operator(console_deps, _PASSWORD)
    program_invite, phone_invite = await mint(rig, "build bot"), await mint(rig, "phone")
    signer, passkey = Ed25519Signer.generate(), SoftPasskey(ORIGIN, backed_up=True)
    program = _Enrolled(program_invite, signer, await redeem_program(rig, program_invite, signer))
    phone = _Enrolled(phone_invite, passkey, await redeem_browser(rig, phone_invite, passkey))
    # The program narrowly and only until tomorrow; the phone with the proposed set.
    tomorrow = rig.clock.now() + timedelta(days=1)
    narrow = approval(capabilities=_NARROW, expires_at=tomorrow, actor=console.id)
    await approve(rig.deps, program.device_id, narrow)
    await approve(rig.deps, phone.device_id, approval(actor=console.id))
    return _Cast(console, program, phone)


async def _live_through(rig: Enrolment, cast: _Cast) -> tuple[Revocation, int]:
    """Lose the phone (lock, unlock, revoke), replay a spent code, then let the program lapse."""
    rig.goals.submit(cast.phone.device_id, [_GOAL])
    await lock(rig.deps, cast.phone.device_id, cast.console.id, LockReason.REMOTE_LOCK)
    await unlock(rig.deps, cast.phone.device_id, cast.console.id)
    revocation = await revoke(rig.deps, cast.phone.device_id, cast.console.id, cancel_goals=True)
    with pytest.raises(EnrolmentRefusedError):
        await redeem_program(rig, cast.program.invite)
    rig.clock.advance(timedelta(days=2).total_seconds())
    return revocation, await expire_due(rig.deps)


async def _kinds_about(rig: Enrolment, device_id: str) -> list[str]:
    """Every trail kind recorded about ``device_id``, in trail order."""
    return [event.kind for event in await rig.events() if event.subject_id == device_id]


async def _secrets(rig: Enrolment, cast: _Cast) -> list[str]:
    """Every value that must never reach the trail, in each form it could take."""
    assert isinstance(cast.program.key, Ed25519Signer)
    assert isinstance(cast.phone.key, SoftPasskey)
    signature = ed25519_proof(rig, cast.program.invite.code, cast.program.key).signature
    values = [_PASSWORD, signature, b64url_encode(cast.phone.key.credential_id)]
    for invite in (cast.program.invite, cast.phone.invite):
        values += [invite.code, invite.code.replace("-", ""), invite_code_hash(invite.code)]
    for device_id in (cast.console.id, cast.program.device_id, cast.phone.device_id):
        device = await rig.store.get_device(device_id)
        assert device.public_key is not None
        values += [device.public_key, b64url_decode(device.public_key).hex()]
    return values


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_a_program_and_a_browser_enrol_end_to_end(kind: str, tmp_path: Path) -> None:
    rig = await _rig(kind, tmp_path)
    cast = await _enrol_both(rig)

    revocation, expired = await _live_through(rig, cast)

    program = await rig.store.get_device(cast.program.device_id)
    phone = await rig.store.get_device(cast.phone.device_id)
    assert (program.status, program.capabilities, program.interactive) == (
        _S.EXPIRED,
        _NARROW,
        False,
    )
    assert (phone.status, phone.interactive, revocation.goals_cancelled) == (
        _S.REVOKED,
        True,
        (_GOAL,),
    )
    assert expired == 1
    assert (await rig.store.get_device(cast.console.id)).status is _S.APPROVED
    assert rig.offboarder.offboarded == [
        (phone.id, _S.LOCKED),
        (phone.id, _S.REVOKED),
        (program.id, _S.EXPIRED),
    ]
    lived = ["invited", "pending", "approved"]
    assert await _kinds_about(rig, phone.id) == [
        f"guard.entrance_{kind}" for kind in (*lived, "locked", "unlocked", "revoked")
    ]
    assert await _kinds_about(rig, program.id) == [
        f"guard.entrance_{kind}" for kind in (*lived, "redeem_failed", "expired")
    ]


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_no_trail_event_of_a_whole_lifecycle_carries_a_secret(
    kind: str, tmp_path: Path
) -> None:
    rig = await _rig(kind, tmp_path)
    cast = await _enrol_both(rig)

    await _live_through(rig, cast)

    forbidden = await _secrets(rig, cast)
    for event in await rig.events():
        dumped = json.dumps(event.model_dump(mode="json"))
        assert [value for value in forbidden if value in dumped] == [], event.kind


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_every_edge_told_every_other_device_about_itself(kind: str, tmp_path: Path) -> None:
    rig = await _rig(kind, tmp_path)
    minted = await mint(rig)
    redemption = await redeem_program(rig, minted)
    await approve(rig.deps, redemption.device_id, approval())

    kinds = [(notice.device_id, notice.kind) for notice in rig.notifier.notices]
    event_ids = {event.id for event in await rig.events()}

    assert kinds == [
        (minted.device_id, "guard.entrance_invited"),
        (minted.device_id, "guard.entrance_pending"),
        (minted.device_id, "guard.entrance_approved"),
    ]
    assert {notice.event_id for notice in rig.notifier.notices} <= event_ids
