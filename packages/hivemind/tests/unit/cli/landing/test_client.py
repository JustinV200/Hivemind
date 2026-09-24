"""Test hivemind.cli.landing.client over a real Entrance: redeem, log in, step up, refusals.

Every test runs a real Hive Entrance on loopback ports over a real Queen (``builders.entrance.
serving``); the operator's console there is the test builder's own client, and the device under
test is the CLI's ``LandingClient``, so a pass means the CLI speaks the Landing Board exactly as
the Entrance expects: an invite redeemed with a fresh key, a login with the key and the password,
a step-up an interactive device answers on its own, a held request a program cannot answer, and
every refusal as one sentence.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/client.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from builders.entrance.auth import PASSWORD, WRONG_PASSWORD
from builders.entrance.serving import RigOptions, ServingRig, serving
from pydantic import SecretStr

from hivemind.cli.landing import (
    DeviceKey,
    EntranceUnreachableError,
    LandingClient,
    LandingRefusedError,
    entrance_address,
    open_http,
    signed_in,
)
from hivemind.entrance.enrol import DeviceDescription
from hivemind.entrance.models import DeviceView, GoalAccepted, GoalSubmission
from waggle.clock import SystemClock
from waggle.signing import Ed25519Signer

_LAPTOP = DeviceDescription(name="laptop", platform="Linux", user_agent="hive-cli/test")
_SUBMITTER = ("entrance:submit", "entrance:answer", "observe")


@asynccontextmanager
async def _client(rig: ServingRig, remote: bool = False) -> AsyncIterator[LandingClient]:
    """The CLI's client for one of the rig's listeners."""
    address = entrance_address(rig.remote_url if remote else rig.loopback_url)
    async with open_http(address) as http:
        yield LandingClient(http, address, rig.hive_id, rig.clock)


async def _redeemed(rig: ServingRig, client: LandingClient) -> DeviceKey:
    """Mint an invite as the console, then redeem it with the CLI's client and a fresh key."""
    console, session = await rig.console_session()
    invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "laptop"})
    signer = Ed25519Signer.generate()
    redemption = await client.redeem(invite.json()["code"], signer, _LAPTOP)
    return DeviceKey(redemption.device_id, signer)


async def _approve(rig: ServingRig, key: DeviceKey, **terms: object) -> None:
    """Approve ``key``'s device as the console on loopback."""
    console, session = await rig.console_session()
    body = {"name": "laptop", "capabilities": list(_SUBMITTER), "spend_cap_usd_per_day": 5.0}
    body.update(terms)
    path = f"/v1/entrance/pending/{key.device_id}/approve"
    approved = await console.call(session, "POST", path, body)
    assert approved.status_code == 200, approved.text


async def test_a_redeemed_and_approved_device_logs_in_calls_and_logs_out() -> None:
    async with serving() as rig, _client(rig) as client:
        key = await _redeemed(rig, client)
        await _approve(rig, key)

        async with signed_in(client, key, SecretStr(PASSWORD)) as board:
            me = await board.call("GET", "/v1/devices/me", None, DeviceView)
            credential = board.session.credential
        with pytest.raises(LandingRefusedError) as after_logout:
            await client.logout(credential)

    assert me.id == key.device_id and me.status.value == "APPROVED"
    assert after_logout.value.status == 401


async def test_a_wrong_password_is_refused_without_saying_which_factor_failed() -> None:
    async with serving() as rig, _client(rig) as client:
        key = await _redeemed(rig, client)
        await _approve(rig, key)

        with pytest.raises(LandingRefusedError) as refused:
            await client.login(key, SecretStr(WRONG_PASSWORD))

    assert refused.value.status == 401
    assert "pending approval, locked or revoked" in str(refused.value)
    assert WRONG_PASSWORD not in str(refused.value)


async def test_a_device_still_pending_approval_cannot_log_in() -> None:
    async with serving() as rig, _client(rig) as client:
        key = await _redeemed(rig, client)

        with pytest.raises(LandingRefusedError) as refused:
            await client.login(key, SecretStr(PASSWORD))

    assert refused.value.status == 401


async def test_an_interactive_device_steps_up_on_its_own_when_a_goal_passes_its_cap() -> None:
    async with serving() as rig, _client(rig) as client:
        key = await _redeemed(rig, client)
        # A daily cap below one goal's worth: the goal needs a step-up, which a person can give.
        await _approve(rig, key, spend_cap_usd_per_day=0.5, interactive=True)

        async with signed_in(client, key, SecretStr(PASSWORD)) as board:
            accepted = await board.call(
                "POST", "/v1/goals", GoalSubmission(text="tidy the garden"), GoalAccepted
            )

    assert accepted.id.startswith("goalreq_")


async def test_a_program_that_cannot_step_up_is_told_the_pending_confirmation() -> None:
    async with serving() as rig, _client(rig) as client:
        key = await _redeemed(rig, client)
        await _approve(rig, key, spend_cap_usd_per_day=0.5)

        async with signed_in(client, key, SecretStr(PASSWORD)) as board:
            with pytest.raises(LandingRefusedError) as held:
                await board.call(
                    "POST", "/v1/goals", GoalSubmission(text="tidy the garden"), GoalAccepted
                )

    pending_id = held.value.pending_id
    assert pending_id is not None and pending_id.startswith("pend_")
    assert not held.value.wants_step_up
    assert f"held as pending confirmation {pending_id}" in str(held.value)


async def test_a_route_beyond_the_devices_capabilities_is_refused_naming_it() -> None:
    async with serving() as rig, _client(rig) as client:
        key = await _redeemed(rig, client)
        await _approve(rig, key, capabilities=["entrance:submit"])

        async with signed_in(client, key, SecretStr(PASSWORD)) as board:
            with pytest.raises(LandingRefusedError) as denied:
                await board.call("GET", "/v1/devices", None, DeviceView)

    assert denied.value.status == 403
    assert "observe" in str(denied.value)


async def test_a_loopback_only_route_is_absent_from_the_remote_listener() -> None:
    async with serving(RigOptions(remote=True)) as rig, _client(rig, remote=True) as client:
        key = await _redeemed(rig, client)
        await _approve(rig, key)

        async with signed_in(client, key, SecretStr(PASSWORD)) as board:
            with pytest.raises(LandingRefusedError) as absent:
                await board.call(
                    "POST", f"/v1/entrance/pending/{key.device_id}/deny", None, DeviceView
                )

    assert absent.value.status == 404
    assert "does not exist on the remote listener" in str(absent.value)


async def test_an_entrance_that_does_not_answer_is_unreachable() -> None:
    # A port nothing listens on: bound, then closed, so it is free and silent.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    address = entrance_address(f"http://127.0.0.1:{port}")

    async with open_http(address) as http:
        client = LandingClient(http, address, "hive_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3", SystemClock())
        with pytest.raises(EntranceUnreachableError):
            await client.login(DeviceKey("device_x", Ed25519Signer.generate()), SecretStr("x"))
