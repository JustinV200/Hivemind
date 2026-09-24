"""Tests for hivemind.entrance.auth.login.flow: the key proof first, then the password.

Every ADR-0033 login rule has a test here: an invalid proof never reaches the password check,
never counts against the device and is charged to its address; five valid proofs with a wrong
password lock the device (through the enrolment step's lock); a success resets the count; the
console logs in only on loopback; a device that is not approved, a reused challenge, a missing
binding key and a remote login while reduced are refused; and nothing a client sent as a
credential ever reaches the trail or an error.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/login/flow.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.login.flow for the module under test.
"""

from __future__ import annotations

import json

import pytest
from builders.entrance import (
    ADDRESS,
    LOOPBACK,
    PASSWORD,
    REMOTE,
    STORE_IDENTITY,
    WRONG_PASSWORD,
    AuthRig,
    BrowserKey,
    admitted,
    admitted_browser,
    admitted_console,
    admitted_program,
    auth_rig,
    browser_login,
    make_device,
    program_login,
    sign_b64url,
)

from hivemind.entrance.auth import (
    BindingKind,
    DeviceProof,
    Listener,
    begin_login,
    finish_login,
    login_string,
)
from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.errors import AuthenticationFailedError
from hivemind.entrance.reducer import EntranceMode
from waggle.signing import Ed25519Signer

_FAILED = "guard.entrance_login_failed"


async def _reasons(auth: AuthRig) -> list[object]:
    """Return the reason of every recorded login failure, in trail order."""
    return [event.payload["reason"] for event in await auth.enrolment.events(_FAILED)]


async def test_a_program_logs_in_with_its_key_and_the_password() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)

    opened = await program_login(auth, device, signer)

    session = opened.session
    assert (session.binding_kind, session.binding_key) == (BindingKind.ED25519, device.public_key)
    assert (session.listener, session.network, session.needs_step_up) == (
        Listener.LOOPBACK,
        "127.0.0.0/24",
        False,
    )
    stored = await auth.store.get_device(device.id)
    assert (stored.last_seen_at, stored.last_network) == (auth.clock.now(), "127.0.0.0/24")
    assert auth.hasher.verifications == 1


async def test_a_browser_logs_in_with_its_passkey_and_binds_its_webcrypto_key() -> None:
    auth = await auth_rig()
    device, passkey = await admitted_browser(auth.enrolment)
    key = BrowserKey()

    opened = await browser_login(auth, device, passkey, key)

    assert (opened.session.binding_kind, opened.session.binding_key) == (
        BindingKind.P256,
        key.public_key,
    )
    assert (await auth.store.get_device(device.id)).sign_count == passkey.sign_count == 1


async def test_an_invalid_proof_never_reaches_the_password_or_counts_against_the_device() -> None:
    auth = await auth_rig()
    device, _ = await admitted_program(auth.enrolment)
    impostor = Ed25519Signer.generate()

    for _ in range(30):
        with pytest.raises(AuthenticationFailedError):
            await program_login(auth, device, impostor, REMOTE)

    stored = await auth.store.get_device(device.id)
    assert auth.hasher.verifications == 0
    assert stored.status is DeviceStatus.APPROVED
    assert await auth.store.logins.failures(device.id) == 0
    assert await _reasons(auth) == ["proof"] * 30
    events = await auth.enrolment.events(_FAILED)
    assert {event.payload["address"] for event in events} == {ADDRESS}
    # Every invalid proof was charged to its address: its minute's allowance is spent.
    assert not auth.deps.guards.limiter.allow_address(ADDRESS)
    assert auth.deps.guards.limiter.allow_address("100.64.0.8")


async def test_five_valid_proofs_with_a_wrong_password_lock_the_device() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)

    for attempt in range(1, 6):
        with pytest.raises(AuthenticationFailedError):
            await program_login(auth, device, signer, password=WRONG_PASSWORD)
        if attempt < 5:
            assert (await auth.store.get_device(device.id)).status is DeviceStatus.APPROVED

    assert (await auth.store.get_device(device.id)).status is DeviceStatus.LOCKED
    (locked,) = await auth.enrolment.events("guard.entrance_locked")
    assert locked.payload == {"reason": "lockout"}
    assert await _reasons(auth) == ["password"] * 5
    assert await auth.store.logins.failures(device.id) == 0
    with pytest.raises(AuthenticationFailedError):
        await program_login(auth, device, signer)
    assert (await _reasons(auth))[-1] == "device"


async def test_a_success_resets_the_consecutive_failure_count() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)

    for _ in range(4):
        with pytest.raises(AuthenticationFailedError):
            await program_login(auth, device, signer, password=WRONG_PASSWORD)
    await program_login(auth, device, signer)
    for _ in range(4):
        with pytest.raises(AuthenticationFailedError):
            await program_login(auth, device, signer, password=WRONG_PASSWORD)

    assert await auth.store.logins.failures(device.id) == 4
    assert (await auth.store.get_device(device.id)).status is DeviceStatus.APPROVED


async def test_the_console_logs_in_only_on_the_loopback_listener() -> None:
    auth = await auth_rig()
    console, signer = await admitted_console(auth)

    opened = await program_login(auth, console, signer, LOOPBACK)
    with pytest.raises(AuthenticationFailedError):
        await begin_login(auth.deps, console.id, REMOTE)

    assert opened.session.volatile
    assert await auth.store.sessions.get(opened.session.token_hash) is None
    assert await _reasons(auth) == ["listener"]


async def test_a_device_that_is_not_approved_gets_no_challenge() -> None:
    auth = await auth_rig()
    pending = await admitted(auth.enrolment, DeviceStatus.PENDING)
    stranger = make_device(auth.clock).id

    for device_id in (pending.id, stranger):
        with pytest.raises(AuthenticationFailedError):
            await begin_login(auth.deps, device_id, LOOPBACK)

    events = await auth.enrolment.events(_FAILED)
    assert [event.payload["reason"] for event in events] == ["device", "device"]
    assert [event.subject_id for event in events] == [
        pending.id,
        auth.deps.records.identity.hive_id,
    ]


async def test_a_challenge_is_spent_once_and_only_by_its_own_device() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    other, other_signer = await admitted_program(auth.enrolment)
    challenge = await begin_login(auth.deps, device.id, LOOPBACK)
    hive_id = auth.deps.records.identity.hive_id
    signature = sign_b64url(signer, login_string(hive_id, device.id, challenge.nonce))
    proof = DeviceProof(device.id, challenge.nonce, signature=signature)
    await finish_login(auth.deps, proof, PASSWORD, LOOPBACK)
    stolen = await begin_login(auth.deps, device.id, LOOPBACK)
    borrowed = sign_b64url(other_signer, login_string(hive_id, other.id, stolen.nonce))

    for replay in (proof, DeviceProof(other.id, stolen.nonce, signature=borrowed)):
        with pytest.raises(AuthenticationFailedError):
            await finish_login(auth.deps, replay, PASSWORD, LOOPBACK)

    assert await _reasons(auth) == ["proof", "proof"]
    assert auth.hasher.verifications == 1


async def test_a_browser_must_register_a_real_binding_key_and_a_program_none() -> None:
    auth = await auth_rig()
    browser, _ = await admitted_browser(auth.enrolment)
    program, _ = await admitted_program(auth.enrolment)

    for device_id, binding in (
        (browser.id, None),
        (browser.id, "AQID"),
        (program.id, BrowserKey().public_key),
    ):
        with pytest.raises(AuthenticationFailedError):
            await begin_login(auth.deps, device_id, LOOPBACK, binding)

    assert await _reasons(auth) == ["proof"] * 3


async def test_a_remote_login_is_refused_while_the_entrance_is_reduced() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    event = STORE_IDENTITY.event(
        auth.clock, "guard.reduced", STORE_IDENTITY.hive_id, {"reason": "operator"}
    )
    await auth.store.entrance_mode.change(EntranceMode.OPEN, EntranceMode.REDUCED, event)

    with pytest.raises(AuthenticationFailedError):
        await program_login(auth, device, signer, REMOTE)
    opened = await program_login(auth, device, signer, LOOPBACK)

    assert opened.session.listener is Listener.LOOPBACK
    assert await _reasons(auth) == ["reduced"]


async def test_no_login_puts_a_credential_on_the_trail_or_in_an_error() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    browser, passkey = await admitted_browser(auth.enrolment)
    opened = [
        await program_login(auth, device, signer),
        await browser_login(auth, browser, passkey, BrowserKey()),
    ]
    errors: list[str] = []
    for password in (WRONG_PASSWORD, PASSWORD):
        try:
            await program_login(auth, device, Ed25519Signer.generate(), password=password)
        except AuthenticationFailedError as refused:
            errors.append(str(refused))
    with pytest.raises(AuthenticationFailedError) as wrong:
        await program_login(auth, device, signer, password=WRONG_PASSWORD)
    errors.append(str(wrong.value))

    forbidden = [PASSWORD, WRONG_PASSWORD, *(each.token for each in opened)]
    forbidden += [each.session.token_hash for each in opened]
    for event in await auth.enrolment.events():
        dumped = json.dumps(event.model_dump(mode="json"))
        assert [value for value in forbidden if value in dumped] == [], event.kind
    assert all(value not in error for error in errors for value in forbidden)
