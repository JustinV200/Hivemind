"""Tests for hivemind.entrance.enrol.redeem: redemption by Ed25519 key and by passkey.

Every accepted case runs the real checks (``cryptography``'s Ed25519, the ``webauthn`` library fed
by SoftPasskey); every refusal is checked for its one generic error, its
``guard.entrance_redeem_failed`` event and that nothing else changed. Two redemptions of one code
race through ``asyncio.gather`` over both stores: exactly one wins.

Fits into the Hive:
    Mirrors src/hivemind/entrance/enrol/redeem.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.enrol.redeem for the module under test.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Unpack

import pytest
from builders.entrance import (
    ADDRESS,
    INVITE_TTL,
    ORIGIN,
    PENDING_TTL,
    RELYING_PARTY,
    Enrolment,
    ed25519_proof,
    enrolment_over,
    make_description,
    memory_enrolment,
    mint,
    redeem_browser,
    redeem_changes,
    redeem_program,
    sqlite_enrolment,
)

from hivemind.entrance.auth import (
    KeyKind,
    SoftPasskey,
    b64url_decode,
    b64url_encode,
    key_fingerprint,
    registration_options,
)
from hivemind.entrance.enrol import (
    ENROLMENT_CHALLENGE_TTL,
    DeviceStatus,
    Ed25519Proof,
    EnrolledDevice,
    FakeGoalLedger,
    MintedInvite,
    Redemption,
    cancel_invite,
    invite_code_hash,
    new_invite_code,
    passkey_options,
    redeem_ed25519,
    redeem_passkey,
)
from hivemind.entrance.errors import EnrolmentRefusedError
from hivemind.entrance.store import DeviceChanges, MemoryEntranceStore
from hivemind.pheromone import GuardEvent, MemoryPheromoneTrail, PheromoneEvent
from waggle.clock import FakeClock
from waggle.ids import new_event_id
from waggle.signing import Ed25519Signer

_REFUSED = str(EnrolmentRefusedError())  # The one message every refusal carries.


@pytest.fixture
def rig() -> Enrolment:
    """An enrolment rig over in-memory tables."""
    return memory_enrolment()


async def _refusal(rig: Enrolment, attempt: Awaitable[object]) -> PheromoneEvent:
    """Await a redemption that must be refused; return the refusal's trail event."""
    with pytest.raises(EnrolmentRefusedError) as excinfo:
        await attempt
    assert str(excinfo.value) == _REFUSED
    assert excinfo.value.__cause__ is None
    return (await rig.events("guard.entrance_redeem_failed"))[-1]


async def _still_invited(rig: Enrolment, minted: MintedInvite) -> None:
    """Assert the invite is unspent and its device still INVITED, with no pending event."""
    assert (await rig.store.get_device(minted.device_id)).status is DeviceStatus.INVITED
    assert (await rig.store.get_invite(invite_code_hash(minted.code))).used_at is None
    assert await rig.events("guard.entrance_pending") == ()


def _program(rig: Enrolment, code: str, proof: Ed25519Proof) -> Awaitable[Redemption]:
    """Start a program's redemption of ``code`` with ``proof``."""
    return redeem_ed25519(rig.deps, code, proof, make_description(), ADDRESS)


# ──────────────────────────────────────────────────────────────────────────────
# Accepted redemptions
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_program_redeems_with_its_signed_key_and_waits_for_the_operator(
    rig: Enrolment,
) -> None:
    minted, signer = await mint(rig), Ed25519Signer.generate()

    redemption = await redeem_program(rig, minted, signer)

    device = await rig.store.get_device(minted.device_id)
    assert (device.status, device.key_kind, device.interactive) == (
        DeviceStatus.PENDING,
        KeyKind.ED25519,
        False,
    )
    assert device.public_key == b64url_encode(signer.public_key_bytes)
    assert device.description == make_description()
    assert device.expires_at == rig.clock.now() + PENDING_TTL
    assert (await rig.store.get_invite(invite_code_hash(minted.code))).used_at == rig.clock.now()
    assert redemption == Redemption(
        minted.device_id,
        key_fingerprint(signer.public_key_bytes),
        rig.deps.ceremony.hive_public_key.hex(),
    )


async def test_a_redemption_records_pending_and_tells_every_other_device(rig: Enrolment) -> None:
    minted = await mint(rig)

    redemption = await redeem_program(rig, minted)

    (event,) = await rig.events("guard.entrance_pending")
    assert (event.subject_id, event.actor) == (minted.device_id, minted.device_id)
    assert event.payload == {
        "fingerprint": redemption.fingerprint,
        "key_kind": "ed25519",
        "address": ADDRESS,
    }
    assert rig.notifier.notices[-1].event_id == event.id
    assert rig.notifier.notices[-1].kind == "guard.entrance_pending"


async def test_passkey_options_carry_a_challenge_bound_to_the_invite(rig: Enrolment) -> None:
    minted = await mint(rig)

    options = json.loads(await passkey_options(rig.deps, minted.code, ADDRESS))

    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert b64url_decode(options["user"]["id"]) == minted.device_id.encode("ascii")
    assert options["user"]["name"] == "operator"
    challenge = rig.deps.ceremony.challenges.take(
        options["challenge"], invite_code_hash(minted.code)
    )
    assert challenge.challenge_bytes == b64url_decode(options["challenge"])


async def test_a_browser_redeems_with_a_passkey_which_is_always_interactive(
    rig: Enrolment,
) -> None:
    minted, passkey = await mint(rig), SoftPasskey(ORIGIN, backed_up=True)

    redemption = await redeem_browser(rig, minted, passkey)

    device = await rig.store.get_device(minted.device_id)
    assert (device.status, device.key_kind, device.interactive) == (
        DeviceStatus.PENDING,
        KeyKind.PASSKEY,
        True,
    )
    assert device.credential_id == b64url_encode(passkey.credential_id)
    assert (device.rp_id, device.backup_eligible, device.backup_state) == ("localhost", True, True)
    assert redemption.fingerprint == device.fingerprint
    (event,) = await rig.events("guard.entrance_pending")
    assert event.payload["key_kind"] == "passkey"


# ──────────────────────────────────────────────────────────────────────────────
# Refusals: one error, one trail event each, nothing else changed
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("code", ["not a code", "ABCD-EFGH", ""])
async def test_a_malformed_code_is_refused_as_unknown_about_the_hive(
    rig: Enrolment, code: str
) -> None:
    proof = ed25519_proof(rig, new_invite_code(), Ed25519Signer.generate())

    event = await _refusal(rig, _program(rig, code, proof))

    assert event.subject_id == rig.deps.records.identity.hive_id
    assert event.payload == {
        "reason": "unknown_code",
        "step": "redeem",
        "key_kind": "ed25519",
        "address": ADDRESS,
    }


async def test_an_unknown_code_is_refused(rig: Enrolment) -> None:
    code = new_invite_code()

    event = await _refusal(
        rig, _program(rig, code, ed25519_proof(rig, code, Ed25519Signer.generate()))
    )

    assert event.payload["reason"] == "unknown_code"


async def test_a_used_code_is_refused(rig: Enrolment) -> None:
    minted = await mint(rig)
    first = await redeem_program(rig, minted)

    event = await _refusal(rig, redeem_program(rig, minted))

    assert (event.subject_id, event.payload["reason"]) == (minted.device_id, "used_code")
    assert (await rig.store.get_device(minted.device_id)).fingerprint == first.fingerprint


async def test_an_expired_code_is_refused(rig: Enrolment) -> None:
    minted = await mint(rig)
    rig.clock.advance(INVITE_TTL.total_seconds())

    event = await _refusal(rig, redeem_program(rig, minted))

    assert event.payload["reason"] == "expired_code"
    await _still_invited(rig, minted)


async def test_a_cancelled_invite_is_refused(rig: Enrolment) -> None:
    minted = await mint(rig)
    await cancel_invite(rig.deps, minted.device_id, "human")

    event = await _refusal(rig, redeem_program(rig, minted))

    assert event.payload["reason"] == "wrong_state"


def _proofs(rig: Enrolment, code: str) -> list[Ed25519Proof]:
    """Proofs that must not verify: another key's signature, malformed fields, another Hive."""
    signer, other = Ed25519Signer.generate(), Ed25519Signer.generate()
    good = ed25519_proof(rig, code, signer)
    forged = ed25519_proof(rig, code, other)
    elsewhere = memory_enrolment()
    return [
        Ed25519Proof(good.public_key_hex, forged.signature),
        Ed25519Proof(good.public_key_hex.upper(), good.signature),
        Ed25519Proof(good.public_key_hex, good.signature + "="),
        Ed25519Proof(good.public_key_hex, b64url_encode(b"short")),
        ed25519_proof(elsewhere, code, signer),
    ]


async def test_every_ed25519_proof_that_does_not_verify_is_refused(rig: Enrolment) -> None:
    minted = await mint(rig)

    for proof in _proofs(rig, minted.code):
        event = await _refusal(rig, _program(rig, minted.code, proof))
        assert event.payload["reason"] == "bad_proof"
    await _still_invited(rig, minted)


@pytest.mark.parametrize(
    "passkey",
    [
        SoftPasskey("https://evil.example"),  # The page is not the Entrance's own origin.
        SoftPasskey(ORIGIN, user_verified=False),  # Touched, but nobody was verified.
    ],
)
async def test_a_passkey_ceremony_that_does_not_verify_is_refused(
    rig: Enrolment, passkey: SoftPasskey
) -> None:
    minted = await mint(rig)

    event = await _refusal(rig, redeem_browser(rig, minted, passkey))

    assert event.payload == {
        "reason": "bad_proof",
        "step": "redeem",
        "key_kind": "passkey",
        "address": ADDRESS,
    }
    await _still_invited(rig, minted)


async def test_a_passkey_answering_a_challenge_never_issued_is_refused(rig: Enrolment) -> None:
    minted = await mint(rig)
    options = registration_options(RELYING_PARTY, os.urandom(32), b"device", "operator")
    registration = SoftPasskey(ORIGIN).create(options)

    event = await _refusal(
        rig, redeem_passkey(rig.deps, minted.code, registration, make_description(), ADDRESS)
    )

    assert event.payload["reason"] == "bad_proof"


async def test_a_passkey_answering_another_invites_challenge_is_refused(rig: Enrolment) -> None:
    ours, theirs = await mint(rig), await mint(rig, "laptop")
    registration = SoftPasskey(ORIGIN).create(await passkey_options(rig.deps, theirs.code, ADDRESS))

    event = await _refusal(
        rig, redeem_passkey(rig.deps, ours.code, registration, make_description(), ADDRESS)
    )

    assert (event.subject_id, event.payload["reason"]) == (ours.device_id, "bad_proof")
    await _still_invited(rig, ours)


async def test_a_passkey_answering_an_expired_challenge_is_refused(rig: Enrolment) -> None:
    minted = await mint(rig)
    registration = SoftPasskey(ORIGIN).create(await passkey_options(rig.deps, minted.code, ADDRESS))
    rig.clock.advance(ENROLMENT_CHALLENGE_TTL.total_seconds())

    event = await _refusal(
        rig, redeem_passkey(rig.deps, minted.code, registration, make_description(), ADDRESS)
    )

    assert event.payload["reason"] == "bad_proof"


async def test_garbage_instead_of_a_registration_is_refused(rig: Enrolment) -> None:
    minted = await mint(rig)

    event = await _refusal(
        rig, redeem_passkey(rig.deps, minted.code, "{}", make_description(), ADDRESS)
    )

    assert event.payload["reason"] == "bad_proof"


async def test_options_for_a_code_that_cannot_be_redeemed_are_refused(rig: Enrolment) -> None:
    event = await _refusal(rig, passkey_options(rig.deps, new_invite_code(), ADDRESS))

    assert (event.payload["step"], event.payload["reason"]) == ("options", "unknown_code")
    assert len(rig.deps.ceremony.challenges) == 0


async def test_an_address_that_is_not_one_is_recorded_as_unreadable(rig: Enrolment) -> None:
    event = await _refusal(rig, passkey_options(rig.deps, "x", "evil\naddress"))

    assert event.payload["address"] == "unreadable"


# ──────────────────────────────────────────────────────────────────────────────
# Races: one code, two redemptions
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_two_redemptions_of_one_code_race_and_exactly_one_wins(
    kind: str, tmp_path: Path
) -> None:
    rig = memory_enrolment() if kind == "memory" else await sqlite_enrolment(tmp_path / "h.db")
    minted = await mint(rig)

    results = await asyncio.gather(
        redeem_program(rig, minted), redeem_browser(rig, minted), return_exceptions=True
    )

    wins = [result for result in results if isinstance(result, Redemption)]
    assert len(wins) == 1
    assert sum(isinstance(result, EnrolmentRefusedError) for result in results) == 1
    device = await rig.store.get_device(minted.device_id)
    assert (device.status, device.fingerprint) == (DeviceStatus.PENDING, wins[0].fingerprint)
    assert len(await rig.events("guard.entrance_pending")) == 1
    (failed,) = await rig.events("guard.entrance_redeem_failed")
    assert failed.payload["reason"] == "used_code"


class _RacingStore(MemoryEntranceStore):
    """Tables where another decision commits just before a redemption's atomic step."""

    def __init__(self, trail: MemoryPheromoneTrail) -> None:
        """Start with no interlude."""
        super().__init__(trail)
        self.interlude: Callable[[str, GuardEvent], Awaitable[object]] | None = None

    async def redeem_invite(
        self,
        code_hash: str,
        used_at: datetime,
        event: GuardEvent,
        **changes: Unpack[DeviceChanges],
    ) -> EnrolledDevice:
        """Run the interlude once (it may redeem too), then redeem as asked."""
        interlude, self.interlude = self.interlude, None
        if interlude is not None:
            await interlude(code_hash, event)
        return await super().redeem_invite(code_hash, used_at, event, **changes)


def _racing_rig() -> tuple[Enrolment, _RacingStore]:
    """An enrolment rig over racing tables."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = _RacingStore(trail)
    return enrolment_over(store, trail, clock, FakeGoalLedger()), store


async def test_a_redemption_beaten_to_the_atomic_step_is_refused_as_used() -> None:
    rig, store = _racing_rig()
    minted = await mint(rig)

    async def rival(code_hash: str, event: GuardEvent) -> object:
        """Another program redeems the same code first, with its own key and event."""
        rival_event = event.model_copy(update={"id": new_event_id(rig.clock)})
        return await store.redeem_invite(code_hash, event.at, rival_event, **redeem_changes())

    store.interlude = rival
    event = await _refusal(rig, redeem_program(rig, minted))

    assert event.payload["reason"] == "used_code"
    assert len(await rig.events("guard.entrance_pending")) == 1


async def test_a_redemption_whose_invite_is_cancelled_meanwhile_is_refused() -> None:
    rig, store = _racing_rig()
    minted = await mint(rig)

    async def cancel(code_hash: str, event: GuardEvent) -> object:
        """The operator cancels the invite while the device is proving its key."""
        return await cancel_invite(rig.deps, minted.device_id, "human")

    store.interlude = cancel
    event = await _refusal(rig, redeem_program(rig, minted))

    assert event.payload["reason"] == "wrong_state"
    assert (await rig.store.get_invite(invite_code_hash(minted.code))).used_at is None
