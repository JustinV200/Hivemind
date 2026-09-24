"""Tests for hivemind.entrance.auth.step_up.ceremony: a step-up needs a person.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/step_up/ceremony.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.step_up.ceremony for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.entrance import (
    ADDRESS,
    LOOPBACK,
    PASSWORD,
    REMOTE,
    WRONG_PASSWORD,
    AuthRig,
    BrowserKey,
    admitted_browser,
    admitted_console,
    admitted_program,
    auth_rig,
    browser_login,
    program_login,
    sign_b64url,
    signed_request,
)

from hivemind.entrance.auth import (
    ActionKind,
    AuthenticatedSession,
    DeviceProof,
    FakePeerEndpointSource,
    Listener,
    OpenedSession,
    StepUpReason,
    authenticate_request,
    begin_login,
    login_string,
    requires_step_up,
    step_up,
    step_up_challenge,
)
from hivemind.entrance.errors import AuthenticationFailedError, StepUpUnavailableError
from waggle.signing import Ed25519Signer


async def _admit(
    auth: AuthRig, opened: OpenedSession, key: Ed25519Signer | BrowserKey
) -> AuthenticatedSession:
    """Authenticate one signed request on ``opened``, as a route would before stepping up."""
    arrival = REMOTE if opened.session.listener is Listener.REMOTE else LOOPBACK
    request = signed_request(opened.token, key, auth.clock.now(), arrival)
    return await authenticate_request(auth.book, request, auth.clock.now())


def _ed25519_proof(
    auth: AuthRig, session: AuthenticatedSession, signer: Ed25519Signer
) -> DeviceProof:
    """Answer a fresh step-up challenge for ``session`` with ``signer``."""
    challenge = step_up_challenge(auth.deps, session)
    hive_id = auth.deps.records.identity.hive_id
    signature = sign_b64url(signer, login_string(hive_id, session.device.id, challenge.nonce))
    return DeviceProof(session.device.id, challenge.nonce, signature=signature)


async def test_the_console_steps_up_with_a_fresh_proof_and_the_password() -> None:
    auth = await auth_rig()
    console, signer = await admitted_console(auth)
    opened = await program_login(auth, console, signer)
    session = await _admit(auth, opened, signer)
    proof = _ed25519_proof(auth, session, signer)

    stepped = await step_up(auth.deps, session, proof, PASSWORD, LOOPBACK)

    assert stepped.stepped_up_until == auth.clock.now() + timedelta(minutes=5)
    assert not session.stepped_up
    assert (await _admit(auth, opened, signer)).stepped_up
    (event,) = await auth.enrolment.events("guard.entrance_step_up")
    assert (event.actor, event.payload["key_kind"], event.payload["cleared_travel_lock"]) == (
        console.id,
        "ed25519",
        False,
    )
    assert auth.enrolment.notifier.notices[-1].event_id == event.id


async def test_a_passkey_steps_up_with_a_fresh_assertion_alone() -> None:
    auth = await auth_rig()
    device, passkey = await admitted_browser(auth.enrolment)
    key = BrowserKey()
    opened = await browser_login(auth, device, passkey, key)
    session = await _admit(auth, opened, key)
    challenge = step_up_challenge(auth.deps, session)
    assert challenge.passkey_options is not None
    proof = DeviceProof(
        device.id, challenge.nonce, assertion=passkey.get(challenge.passkey_options)
    )

    await step_up(auth.deps, session, proof, None, LOOPBACK)

    after = await _admit(auth, opened, key)
    assert after.stepped_up
    assert requires_step_up(after, ActionKind.KEY_CHANGE, step_up_spend=5.0) is None
    assert (await auth.store.get_device(device.id)).sign_count == 2


async def test_a_device_no_person_types_at_cannot_step_up() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    session = await _admit(auth, await program_login(auth, device, signer), signer)
    login = await begin_login(auth.deps, device.id, LOOPBACK)

    with pytest.raises(StepUpUnavailableError):
        step_up_challenge(auth.deps, session)
    with pytest.raises(StepUpUnavailableError):
        await step_up(auth.deps, session, DeviceProof(device.id, login.nonce), PASSWORD, LOOPBACK)


async def test_a_wrong_or_missing_password_counts_against_the_device() -> None:
    auth = await auth_rig()
    console, signer = await admitted_console(auth)
    session = await _admit(auth, await program_login(auth, console, signer), signer)

    for password in (WRONG_PASSWORD, None):
        proof = _ed25519_proof(auth, session, signer)
        with pytest.raises(AuthenticationFailedError):
            await step_up(auth.deps, session, proof, password, LOOPBACK)

    events = await auth.enrolment.events("guard.entrance_login_failed")
    assert [(event.payload["reason"], event.payload["step"]) for event in events] == [
        ("password", "step_up"),
        ("password", "step_up"),
    ]
    assert await auth.store.logins.failures(console.id) == 2


async def test_a_login_challenge_is_never_spent_as_a_step_up() -> None:
    auth = await auth_rig()
    console, signer = await admitted_console(auth)
    session = await _admit(auth, await program_login(auth, console, signer), signer)
    login = await begin_login(auth.deps, console.id, LOOPBACK)
    hive_id = auth.deps.records.identity.hive_id
    signature = sign_b64url(signer, login_string(hive_id, console.id, login.nonce))

    proof = DeviceProof(console.id, login.nonce, signature=signature)

    with pytest.raises(AuthenticationFailedError):
        await step_up(auth.deps, session, proof, PASSWORD, LOOPBACK)

    assert auth.hasher.verifications == 1  # The login's own, never the refused step-up's.


async def test_a_step_up_clears_the_travel_lock_and_trusts_the_network() -> None:
    source = FakePeerEndpointSource({ADDRESS: "203.0.113.0/24"})
    auth = await auth_rig(source=source)
    device, passkey = await admitted_browser(auth.enrolment)
    key = BrowserKey()
    opened = await browser_login(auth, device, passkey, key, REMOTE)
    flagged = await _admit(auth, opened, key)
    challenge = step_up_challenge(auth.deps, flagged)
    assert challenge.passkey_options is not None
    proof = DeviceProof(
        device.id, challenge.nonce, assertion=passkey.get(challenge.passkey_options)
    )

    before = requires_step_up(flagged, step_up_spend=5.0)
    await step_up(auth.deps, flagged, proof, None, REMOTE)
    cleared = await _admit(auth, opened, key)
    again = await browser_login(auth, device, passkey, key, REMOTE)

    assert before is StepUpReason.NEW_NETWORK
    assert requires_step_up(cleared, step_up_spend=5.0) is None
    assert await auth.store.logins.networks(device.id) == frozenset({"203.0.113.0/24"})
    assert not again.session.needs_step_up
