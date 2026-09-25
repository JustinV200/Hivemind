"""Test hivemind.cli.landing.signing against the Entrance's own verifiers, byte for byte.

Every signature the CLI makes is checked here by the code the Hive Entrance itself runs: a signed
request by ``authenticate_request``, a socket's first frame by ``authenticate_websocket``, a login
answer by the real login flow and an enrolment proof by the real redemption. Nothing is compared
against a copy of a signed string.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/signing.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from builders.entrance import (
    ADDRESS,
    LOOPBACK,
    PASSWORD,
    AuthRig,
    admitted_program,
    auth_rig,
    make_description,
    memory_enrolment,
    mint,
    program_login,
)

from hivemind.cli.landing import (
    Credential,
    DeviceKey,
    OutgoingRequest,
    Stamp,
    enrol_signature,
    first_frame,
    login_signature,
    request_headers,
    sign,
)
from hivemind.entrance.auth import (
    DeviceProof,
    SignedRequest,
    SocketOpening,
    authenticate_request,
    authenticate_websocket,
    b64url_decode,
    begin_login,
    finish_login,
    verify_ed25519,
)
from hivemind.entrance.enrol import DeviceStatus, Ed25519Proof, redeem_ed25519
from hivemind.entrance.errors import AuthenticationFailedError
from waggle.clock import FakeClock
from waggle.signing import Ed25519Signer

_BODY = b'{"text":"tidy the garden"}'
_TOKEN = "secret-token-value"  # noqa: S105 -- a test's stand-in token, not a credential


async def _logged_in() -> tuple[AuthRig, Credential]:
    """An approved program logged in through the real flow, and the CLI's credential for it."""
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    opened = await program_login(auth, device, signer)
    return auth, Credential(token=opened.token, key=DeviceKey(device.id, signer))


async def test_signed_request_headers_are_admitted_by_the_entrances_own_verifier() -> None:
    auth, credential = await _logged_in()
    request = OutgoingRequest("POST", "/v1/goals?draft=1", _BODY)

    headers = request_headers(credential, request, Stamp.now(auth.clock))
    admitted = await authenticate_request(
        auth.book,
        SignedRequest("POST", "/v1/goals", "draft=1", _BODY, headers, LOOPBACK),
        auth.clock.now(),
    )

    assert admitted.device.id == credential.key.device_id


async def test_a_body_changed_after_signing_is_refused_by_the_verifier() -> None:
    auth, credential = await _logged_in()
    request = OutgoingRequest("POST", "/v1/goals", _BODY)
    headers = request_headers(credential, request, Stamp.now(auth.clock))
    tampered = SignedRequest("POST", "/v1/goals", "", _BODY + b" ", headers, LOOPBACK)

    with pytest.raises(AuthenticationFailedError):
        await authenticate_request(auth.book, tampered, auth.clock.now())


async def test_a_first_frame_is_admitted_by_the_entrances_socket_check() -> None:
    auth, credential = await _logged_in()
    frame = first_frame(credential, "/v1/chat/stream?after=3", Stamp.now(auth.clock))
    opening = SocketOpening(frame, "/v1/chat/stream", "after=3", None, LOOPBACK)

    admitted = await authenticate_websocket(auth.book, opening, auth.clock.now())

    assert admitted.device.id == credential.key.device_id


async def test_a_login_signature_opens_a_session_through_the_real_login_flow() -> None:
    auth = await auth_rig()
    device, signer = await admitted_program(auth.enrolment)
    hive_id = auth.enrolment.deps.records.identity.hive_id
    challenge = await begin_login(auth.deps, device.id, LOOPBACK)

    signature = login_signature(hive_id, DeviceKey(device.id, signer), challenge.nonce)
    proof = DeviceProof(device.id, challenge.nonce, signature=signature)
    opened = await finish_login(auth.deps, proof, PASSWORD, LOOPBACK)

    assert opened.token


async def test_an_enrol_signature_redeems_an_invite_through_the_real_flow() -> None:
    rig = memory_enrolment()
    minted = await mint(rig, "laptop")
    signer = Ed25519Signer.generate()
    hive_id = rig.deps.records.identity.hive_id

    # A person may retype the code in lower case without its dashes; the proof still holds.
    typed = minted.code.replace("-", "").lower()
    proof = Ed25519Proof(signer.public_key_bytes.hex(), enrol_signature(hive_id, typed, signer))
    redemption = await redeem_ed25519(rig.deps, typed, proof, make_description(), ADDRESS)

    stored = await rig.store.get_device(redemption.device_id)
    assert stored.status is DeviceStatus.PENDING


def test_sign_is_unpadded_base64url_of_the_raw_ed25519_signature() -> None:
    signer = Ed25519Signer.generate()

    signature = sign(signer, b"hive-request-v1\nGET\n/v1/inbox")

    assert "=" not in signature and "+" not in signature and "/" not in signature
    raw = b64url_decode(signature)
    assert len(raw) == 64
    assert verify_ed25519(signer.public_key_bytes, b"hive-request-v1\nGET\n/v1/inbox", raw)


def test_stamp_now_takes_the_clocks_whole_second_and_a_fresh_nonce() -> None:
    clock = FakeClock(datetime(2026, 9, 24, 12, 0, 0, 750_000, tzinfo=UTC))

    first, second = Stamp.now(clock), Stamp.now(clock)

    assert first.timestamp == second.timestamp == int(clock.now().timestamp())
    assert first.nonce != second.nonce
    assert len(b64url_decode(first.nonce)) >= 16


def test_no_repr_shows_the_token_or_the_private_key() -> None:
    signer = Ed25519Signer.generate()
    credential = Credential(token=_TOKEN, key=DeviceKey("device_x", signer))

    shown = repr(credential)

    assert _TOKEN not in shown
    assert signer.private_key_bytes.hex() not in shown
    assert "device_x" in shown
