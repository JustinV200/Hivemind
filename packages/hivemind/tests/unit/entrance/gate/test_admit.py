"""Test hivemind.entrance.gate.admit: every request is signed, bound, standing and authorised.

ADR-0041's refusals, over real listeners: a token alone is useless without the binding key, a
signature must cover the request exactly as sent with a fresh timestamp and a single-use nonce, a
session works only on the listener it was opened on, and only an APPROVED device's session admits
anything: a pending device cannot log in, and locking or revoking a device ends its session at
once. A capability denial is recorded at the Guard's Entrance route point.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from dataclasses import replace

from builders.entrance.auth import BrowserKey, sign_b64url
from builders.entrance.landing import LandingSession
from builders.entrance.serving import ProgramGrant, RigOptions, serving

from hivemind.entrance.auth import new_nonce, request_string, sha256_hex
from hivemind.guard import EnforcementPoint
from hivemind.guard.enforcer import DENIED_KIND
from hivemind.pheromone import TrailQuery
from waggle.signing import Ed25519Signer

_TARGET = "/v1/devices/me"  # A read every approved device may make.


def _headers(
    session: LandingSession, stamp: int, nonce: str, signer: Ed25519Signer | BrowserKey
) -> dict[str, str]:
    """Sign a GET of the target at ``stamp`` with ``nonce``, by ``signer``."""
    message = request_string("GET", _TARGET, stamp, nonce, sha256_hex(b""))
    return {
        "Authorization": f"Bearer {session.token}",
        "X-Hive-Timestamp": str(stamp),
        "X-Hive-Nonce": nonce,
        "X-Hive-Signature": sign_b64url(signer, message),
    }


async def test_a_stolen_token_without_the_binding_key_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        thief = replace(session, key=replace(session.key, signer=Ed25519Signer.generate()))

        stolen = await client.call(thief, "GET", _TARGET)
        own = await client.call(session, "GET", _TARGET)

    assert stolen.status_code == 401
    assert own.status_code == 200


async def test_a_request_without_a_signature_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program()

        response = await client.http.get(
            _TARGET, headers={"Authorization": f"Bearer {session.token}"}
        )

    assert response.status_code == 401


async def test_a_signature_over_another_request_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        headers = client.signed_headers(session, "GET", "/v1/devices", b"")

        response = await client.http.get(_TARGET, headers=headers)

    assert response.status_code == 401


async def test_a_stale_timestamp_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        stale = int(rig.clock.now().timestamp()) - 3600

        response = await client.http.get(
            _TARGET, headers=_headers(session, stale, new_nonce(16), session.key.signer)
        )

    assert response.status_code == 401


async def test_a_replayed_nonce_is_refused() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        headers = _headers(
            session, int(rig.clock.now().timestamp()), new_nonce(16), session.key.signer
        )

        first = await client.http.get(_TARGET, headers=headers)
        replayed = await client.http.get(_TARGET, headers=headers)

    assert first.status_code == 200
    assert replayed.status_code == 401


async def test_a_session_opened_on_loopback_is_refused_on_the_remote_listener() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        local, session = await rig.program()
        remote = rig.client(remote=True)

        response = await remote.call(session, "GET", _TARGET)
        at_home = await local.call(session, "GET", _TARGET)

    assert response.status_code == 401
    assert at_home.status_code == 200


async def test_a_pending_device_cannot_log_in() -> None:
    async with serving() as rig:
        console, session = await rig.console_session()
        invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "bot"})
        client = rig.client()
        key = await client.enrol(invite.json()["code"])

        challenge = await client.http.post("/v1/auth/challenge", json={"device_id": key.device_id})

    assert challenge.status_code == 401


async def test_locking_a_device_ends_its_session_at_once() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        console, console_session = await rig.console_session()
        await console.step_up(console_session)

        locked = await console.call(
            console_session, "POST", f"/v1/devices/{session.key.device_id}/lock"
        )
        after = await client.call(session, "GET", _TARGET)

    assert locked.status_code == 200, locked.text
    assert locked.json()["status"] == "LOCKED"
    assert after.status_code == 401


async def test_revoking_a_device_ends_its_session_and_it_cannot_log_in_again() -> None:
    async with serving() as rig:
        client, session = await rig.program()
        console, console_session = await rig.console_session()

        revoked = await console.call(
            console_session,
            "POST",
            f"/v1/devices/{session.key.device_id}/revoke",
            {"cancel_goals": False},
        )
        after = await client.call(session, "GET", _TARGET)
        again = await client.http.post(
            "/v1/auth/challenge", json={"device_id": session.key.device_id}
        )

    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["device"]["status"] == "REVOKED"
    assert after.status_code == 401
    assert again.status_code == 401


async def test_a_capability_denial_is_refused_at_the_entrance_route_point() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("observe",)))

        response = await client.call(session, "POST", "/v1/goals", {"text": "tidy the garden"})
        denied = await rig.deps.trail.query(TrailQuery(kind=DENIED_KIND))

    assert response.status_code == 403
    assert response.json()["capability"] == "entrance:submit"
    assert [event.payload["point"] for event in denied] == [EnforcementPoint.ENTRANCE_ROUTE.value]


async def test_a_route_returning_personal_content_also_needs_the_c2_clearance() -> None:
    async with serving() as rig:
        client, session = await rig.program(ProgramGrant(capabilities=("entrance:submit",)))

        posted = await client.call(session, "POST", "/v1/chat", {"text": "Hello"})
        read = await client.call(session, "GET", "/v1/chat")

    assert posted.status_code == 202
    assert read.status_code == 403
    assert read.json()["capability"] == "honey:clearance:c2"
