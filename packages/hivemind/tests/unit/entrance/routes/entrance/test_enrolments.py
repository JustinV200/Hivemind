"""Test hivemind.entrance.routes.entrance.enrolments: invites, registration, pending, approval.

Over real listeners: a program enrols over HTTP and is approved on loopback; the pending list
names what redeemed; approval exists only on loopback; a browser enrols with a passkey. A device
that could reach no enrolment listener is registered offline by the operator from its public key
and certificate request, waits like any other, and its approval answers with the certificate
issued from that request (a Hive running its authority); a damaged request registers nothing, and
registration, like approval, is never served on the remote listener.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance import program_request
from builders.entrance.serving import ProgramGrant, RigOptions, serving

from hivemind.entrance.enrol import DeviceStatus
from waggle.ids import DeviceId
from waggle.signing import Ed25519Signer

_DAMAGED = "-----BEGIN CERTIFICATE REQUEST-----\nAAAA\n-----END CERTIFICATE REQUEST-----\n"


def _registration(signer: Ed25519Signer, request: str | None = None) -> dict[str, str]:
    """What ``hive entrance register`` sends for a device holding ``signer``."""
    return {
        "name": "field laptop",
        "public_key_hex": signer.public_key_bytes.hex(),
        "certificate_request": request if request is not None else program_request(signer),
    }


async def test_a_program_enrolled_over_http_is_approved_on_loopback_and_logs_in() -> None:
    async with serving() as rig:
        client, session = await rig.program()

        me = await client.call(session, "GET", "/v1/devices/me")

    assert me.status_code == 200, me.text
    assert me.json()["status"] == DeviceStatus.APPROVED.value
    stored = await rig.store.get_device(DeviceId(session.key.device_id))
    assert stored is not None
    assert set(stored.capabilities) == set(ProgramGrant().capabilities)


async def test_the_pending_list_names_a_redeemed_device_for_the_steward() -> None:
    async with serving() as rig:
        console, session = await rig.console_session()
        invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "bot"})
        key = await rig.client().enrol(invite.json()["code"])

        pending = await console.call(session, "GET", "/v1/entrance/pending")

    assert pending.status_code == 200, pending.text
    assert [row["id"] for row in pending.json()["devices"]] == [key.device_id]


async def test_approval_is_not_served_on_the_remote_listener() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        remote = rig.client(remote=True)

        response = await remote.http.post("/v1/entrance/pending/device_x/approve", json={})

    assert response.status_code == 404


async def test_a_browser_enrols_with_a_passkey_and_logs_in_with_its_webcrypto_key() -> None:
    async with serving() as rig:
        client, session = await rig.browser()

        me = await client.call(session, "GET", "/v1/devices/me")

    assert me.status_code == 200, me.text
    stored = await rig.store.get_device(DeviceId(session.key.device_id))
    assert stored.key_kind is not None and stored.key_kind.value == "passkey"
    assert stored.rp_id == "localhost"


async def test_a_device_registered_offline_waits_and_its_approval_carries_its_certificate() -> None:
    async with serving(RigOptions(authority=True)) as rig:
        console, session = await rig.console_session()

        path = "/v1/entrance/register"
        registered = await console.call(
            session, "POST", path, _registration(Ed25519Signer.generate())
        )
        device_id = registered.json()["device_id"]
        pending = await console.call(session, "GET", "/v1/entrance/pending")
        body = {"name": "field laptop", "spend_cap_usd_per_day": 5.0}
        approve = f"/v1/entrance/pending/{device_id}/approve"
        approved = await console.call(session, "POST", approve, body)

    assert registered.status_code == 201, registered.text
    [row] = pending.json()["devices"]
    assert (row["id"], row["certificate_requested"]) == (device_id, True)
    answer = approved.json()
    assert approved.status_code == 200, approved.text
    assert answer["certificate_pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert answer["certificate_serial"] is not None and answer["bundle"] is None


async def test_a_damaged_request_registers_nothing() -> None:
    async with serving(RigOptions(authority=True)) as rig:
        console, session = await rig.console_session()
        body = _registration(Ed25519Signer.generate(), _DAMAGED)

        refused = await console.call(session, "POST", "/v1/entrance/register", body)
        pending = await console.call(session, "GET", "/v1/entrance/pending")

    assert refused.status_code == 403
    assert refused.json()["error"] == "hivemind.entrance.certificate_request_refused"
    assert pending.json()["devices"] == []


async def test_registration_is_not_served_on_the_remote_listener() -> None:
    async with serving(RigOptions(remote=True)) as rig:
        remote = rig.client(remote=True)

        body = _registration(Ed25519Signer.generate())
        response = await remote.http.post("/v1/entrance/register", json=body)

    assert response.status_code == 404
