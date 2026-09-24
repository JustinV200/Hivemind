"""Provide ``hive entrance devices|revoke|steward|unlock``: an admitted device's standing.

Once a device is approved its standing changes only on the Hive Stand's loopback listener
(ADR-0033), and each command here is the console device calling one of those routes. ``devices``
lists every enrolled device with its status, key fingerprint and daily cap. ``revoke`` withdraws a
device for good and says what became of its open goals (``--cancel-goals`` cancels them in the same
step). ``steward`` grants or withdraws ``entrance:steward``, the capability that lets a device
approve others through the steward route after full step-up (only mounted while ``[entrance]
steward_devices`` is on); widening a device's set needs a step-up, which the console gives with
the password it already holds. ``unlock`` returns a locked device to APPROVED; ``unlock --console``
is the one offline path, for the console itself, which cannot log in while locked: it holds the
serve lock (so ``hive serve`` must be stopped) and proves the password by opening the console key.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Registered by
    ``hivemind.cli.entrance.group``. Calls into ``hivemind.cli.entrance.console`` and ``.render``,
    the Landing Board's models and ``hivemind.entrance.enrol.unlock_console``.

Key invariants:
    - A device's capabilities change only through the loopback route, never by writing tables.
    - ``unlock --console`` never runs beside ``hive serve``.

See Also:
    - hivemind.entrance.routes.devices for the routes.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the steward rule.
"""

from __future__ import annotations

from typing import Annotated

import typer

from hivemind.cli.entrance.console import Stand, run_console, run_offline
from hivemind.cli.entrance.render import devices_table, shown
from hivemind.cli.landing import LandingError, SignedIn
from hivemind.cli.stores import JsonOption
from hivemind.entrance.enrol import ConsoleDeps, DeviceStatus, EnrolledDevice, unlock_console
from hivemind.entrance.models import (
    DeviceList,
    DeviceView,
    RevocationView,
    RevokeBody,
    WidenBody,
)

STEWARD = "entrance:steward"  # The capability steward toggles.

DeviceArgument = Annotated[str, typer.Argument(help="The device's id (device_...).")]
OptionalDevice = Annotated[
    str | None, typer.Argument(help="The locked device's id; omit with --console.")
]

__all__ = ["STEWARD", "devices_command", "revoke_command", "steward_command", "unlock_command"]


def devices_command(
    ctx: typer.Context,
    status: Annotated[
        DeviceStatus | None,
        typer.Option("--status", case_sensitive=False, help="Only devices in this status."),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """List every enrolled device: status, name, key fingerprint and daily cap."""

    async def read(board: SignedIn, _stand: Stand) -> DeviceList:
        """Read every device."""
        return await board.call("GET", "/v1/devices", None, DeviceList)

    listed = run_console(ctx, "devices", read)
    devices = [device for device in listed.devices if status is None or device.status is status]
    if as_json:
        typer.echo(DeviceList(devices=devices).model_dump_json(indent=2))
        return
    for line in devices_table(devices):
        typer.echo(line)


def revoke_command(
    ctx: typer.Context,
    device_id: DeviceArgument,
    cancel_goals: Annotated[
        bool, typer.Option("--cancel-goals", help="Cancel its open goals in the same step.")
    ] = False,
) -> None:
    """Revoke a device for good (loopback only), naming what became of its open goals."""

    async def withdraw(board: SignedIn, _stand: Stand) -> RevocationView:
        """Revoke it on loopback."""
        body = RevokeBody(cancel_goals=cancel_goals)
        path = f"/v1/devices/{device_id}/revoke"
        return await board.call("POST", path, body, RevocationView)

    revoked = run_console(ctx, "revoke", withdraw)
    typer.echo(f"Revoked {revoked.device.id} ({shown(revoked.device.name)}).")
    typer.echo(f"  goals cancelled: {', '.join(revoked.goals_cancelled) or 'none'}")
    typer.echo(f"  goals left running: {', '.join(revoked.goals_left_running) or 'none'}")


def steward_command(
    ctx: typer.Context,
    device_id: DeviceArgument,
    on: Annotated[bool, typer.Option("--on/--off", help="Grant entrance:steward, or withdraw it.")],
) -> None:
    """Grant or withdraw entrance:steward: approving other devices after full step-up."""

    async def toggle(board: SignedIn, stand: Stand) -> tuple[DeviceView, bool]:
        """Replace the device's set with steward added or removed (a step-up on loopback)."""
        device = await _device(board, device_id)
        others = [capability for capability in device.capabilities if capability != STEWARD]
        body = WidenBody(capabilities=[*others, STEWARD] if on else others)
        path = f"/v1/devices/{device.id}/capabilities"
        changed = await board.call("POST", path, body, DeviceView)
        return changed, stand.manifest.entrance.steward_devices

    changed, mounted = run_console(ctx, "steward", toggle)
    state = "holds" if STEWARD in changed.capabilities else "no longer holds"
    typer.echo(f"{changed.id} ({shown(changed.name)}) {state} {STEWARD}.")
    # Holding the capability is not enough: the route itself is mounted only by the manifest.
    if on and not mounted:
        typer.echo(
            "Note: [entrance] steward_devices is off, so the steward route is not mounted; a "
            "steward approves nothing until it is switched on."
        )


def unlock_command(
    ctx: typer.Context,
    device_id: OptionalDevice = None,
    console: Annotated[
        bool,
        typer.Option("--console", help="Unlock the Hive Stand console, offline (serve stopped)."),
    ] = False,
) -> None:
    """Unlock a locked device on loopback; --console unlocks the console itself, offline."""
    if console == (device_id is not None):
        raise typer.BadParameter("Name a locked device's id, or pass --console; not both.")
    if console:
        unlocked = run_offline(ctx, "unlock", _unlock_console)
        typer.echo(f"Unlocked the Hive Stand console {unlocked.id}; start hive serve again.")
        return

    async def reopen(board: SignedIn, _stand: Stand) -> DeviceView:
        """Unlock it on loopback."""
        return await board.call("POST", f"/v1/devices/{device_id}/unlock", None, DeviceView)

    device = run_console(ctx, "unlock", reopen)
    typer.echo(f"Unlocked {device.id} ({shown(device.name)}); it may log in again.")


async def _unlock_console(deps: ConsoleDeps, stand: Stand) -> EnrolledDevice:
    """Unlock the console with the password this command read."""
    return await unlock_console(deps, stand.password.get_secret_value())


async def _device(board: SignedIn, device_id: str) -> DeviceView:
    """Return one device's view, or refuse naming the id."""
    listed = await board.call("GET", "/v1/devices", None, DeviceList)
    for device in listed.devices:
        if device.id == device_id:
            return device
    raise LandingError(f"No device {device_id} is enrolled; hive entrance devices lists them.")
