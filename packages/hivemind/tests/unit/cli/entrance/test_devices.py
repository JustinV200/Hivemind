"""Test hivemind.cli.entrance.devices: list, revoke, steward and unlock, over a real hive serve.

The standing commands run as the console device on the loopback listener of ``hive serve``'s own
composition; a device locks itself out with wrong passwords through the CLI's own Landing Board
client; and the console's own offline unlock is refused while the serve lock is held.

Fits into the Hive:
    Mirrors src/hivemind/cli/entrance/devices.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.entrance.auth import PASSWORD, WRONG_PASSWORD
from builders.entrance.stand import (
    Stand,
    json_of,
    landing_client,
    redeem_invite,
    serving_stand,
    set_password,
    stand_manifest,
)
from pydantic import SecretStr
from typer.testing import CliRunner

from hivemind.cli.app import app
from hivemind.cli.entrance import entrance_tables, hold_serve_lock
from hivemind.cli.landing import DeviceKey, LandingRefusedError, signed_in
from hivemind.entrance.enrol import DeviceStatus, trail_kind
from hivemind.entrance.models import DeviceView
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock


async def _approved(stand: Stand, *terms: str) -> DeviceKey:
    """Invite, redeem and approve a laptop; return its key."""
    invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
    key = await redeem_invite(stand, invite["code"])
    approved = await stand.entrance("approve", key.device_id, "--spend-cap", "5", "--yes", *terms)
    assert approved.exit_code == 0, approved.output
    return key


def _statuses(listed: dict[str, list[dict[str, object]]]) -> dict[object, object]:
    """Each device's status, by id, from ``devices --json``."""
    return {row["id"]: row["status"] for row in listed["devices"]}


async def test_the_standing_commands_list_steward_and_revoke_a_device(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    await set_password(path)

    async with serving_stand(path) as (stand, _entrance):
        key = await _approved(stand)
        table = await stand.entrance("devices")
        approved_only = json_of(await stand.entrance("devices", "--status", "approved", "--json"))
        steward_on = await stand.entrance("steward", key.device_id, "--on")
        steward_off = await stand.entrance("steward", key.device_id, "--off")
        revoked = await stand.entrance("revoke", key.device_id, "--cancel-goals")
        after = json_of(await stand.entrance("devices", "--json"))

    assert "Hive Stand console" in table.output and key.device_id in table.output
    assert set(_statuses(approved_only).values()) == {"APPROVED"}
    assert steward_on.exit_code == 0, steward_on.output
    assert "holds entrance:steward" in steward_on.output
    assert "steward_devices is off" in steward_on.output
    assert "no longer holds entrance:steward" in steward_off.output
    assert f"Revoked {key.device_id}" in revoked.output
    assert "goals cancelled: none" in revoked.output
    assert _statuses(after)[key.device_id] == "REVOKED"


async def test_a_device_locked_out_by_wrong_passwords_is_unlocked_on_loopback(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path, "lockout_attempts = 2\n")
    await set_password(path)

    async with serving_stand(path) as (stand, _entrance):
        key = await _approved(stand)
        await _fail_logins(stand, key, times=2)
        locked = json_of(await stand.entrance("devices", "--json"))
        unlocked = await stand.entrance("unlock", key.device_id)
        me = await _log_in(stand, key)

    assert _statuses(locked)[key.device_id] == "LOCKED"
    assert unlocked.exit_code == 0 and "may log in again" in unlocked.output
    assert me.status is DeviceStatus.APPROVED


def test_the_console_is_unlocked_offline_and_only_while_serve_is_stopped(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path)
    runner = CliRunner()
    args = ["entrance", "operator", "password", "--manifest", str(path), "--password-stdin"]
    runner.invoke(app, args, input=f"{PASSWORD}\n")
    manifest = load_manifest(path, {})
    asyncio.run(_lock_console(manifest))
    unlock = ["entrance", "unlock", "--console", "--manifest", str(path), "--password-stdin"]

    with hold_serve_lock(manifest.resolve_path(manifest.hive.db), "hive serve"):
        busy = runner.invoke(app, unlock, input=f"{PASSWORD}\n")
    wrong = runner.invoke(app, unlock, input=f"{WRONG_PASSWORD}\n")
    unlocked = runner.invoke(app, unlock, input=f"{PASSWORD}\n")

    assert busy.exit_code == 1 and "in use by another process" in busy.output
    assert wrong.exit_code == 1
    assert unlocked.exit_code == 0, unlocked.output
    assert "Unlocked the Hive Stand console" in unlocked.output
    assert asyncio.run(_console_status(manifest)) is DeviceStatus.APPROVED


@pytest.mark.parametrize("args", [["unlock"], ["unlock", "device_x", "--console"]])
def test_unlock_takes_a_device_or_the_console_but_not_both(tmp_path: Path, args: list[str]) -> None:
    path = stand_manifest(tmp_path)

    result = CliRunner().invoke(
        app, ["entrance", *args, "--manifest", str(path), "--password-stdin"], input="x\n"
    )

    assert result.exit_code == 2
    assert "Name a locked device's id, or pass --console" in result.output


async def _fail_logins(stand: Stand, key: DeviceKey, times: int) -> None:
    """Log ``key`` in with the wrong password ``times`` times; each is refused."""
    async with landing_client(stand) as client:
        for _ in range(times):
            with pytest.raises(LandingRefusedError):
                await client.login(key, SecretStr(WRONG_PASSWORD))


async def _log_in(stand: Stand, key: DeviceKey) -> DeviceView:
    """Log ``key`` in with the right password and read its own record."""
    async with (
        landing_client(stand) as client,
        signed_in(client, key, SecretStr(PASSWORD)) as board,
    ):
        return await board.call("GET", "/v1/devices/me", None, DeviceView)


async def _lock_console(manifest: HiveManifest) -> None:
    """Lock the console as a lockout would, straight in the tables (serve is not running)."""
    async with entrance_tables(manifest, SystemClock()) as deps:
        console = next(
            device for device in await deps.store.list_devices() if device.loopback_bound
        )
        kind = trail_kind(DeviceStatus.APPROVED, DeviceStatus.LOCKED)
        event = deps.identity.event(deps.clock, kind, console.id, {"reason": "lockout"})
        await deps.store.update_device_status(
            console.id, DeviceStatus.APPROVED, DeviceStatus.LOCKED, event
        )


async def _console_status(manifest: HiveManifest) -> DeviceStatus:
    """The console's status in the tables."""
    async with entrance_tables(manifest, SystemClock()) as deps:
        consoles = [device for device in await deps.store.list_devices() if device.loopback_bound]
        return consoles[0].status
