"""Test hivemind.entrance.routes.entrance.enrolments: invites, the pending list, approval.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.entrance.serving import ProgramGrant, RigOptions, serving

from hivemind.entrance.enrol import DeviceStatus
from waggle.ids import DeviceId


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
