"""Provide ``hive entrance invite|register|pending|approve|deny``: admit a device, on loopback.

Enrolment at the Hive Entrance (the Hive's HTTP door) is invite, key, pending, approval on
loopback (ADR-0033). ``invite`` mints a single-use code for a named device and prints it once:
grouped for reading aloud, as the enrolment link with the code in its fragment, as a terminal QR
code of that link, with the Hive's id and the ``hive remote enrol`` line a laptop or program runs.
``pending`` lists the devices that redeemed an invite and wait. ``approve`` shows the waiting
device's key fingerprint, its passkey backup flags and what it says it is (every string it
supplied escaped), asks for a yes unless ``--yes``, and binds its name, capabilities (the device
role's proposed set when none are named), daily spend cap, expiry and whether a person types at it
(``--interactive``). When the Hive runs its own certificate authority the approval issues the
device's mutual-TLS client certificate: from a program's request (printed by serial and
fingerprint, written to ``--certificate-out`` for a device registered offline), or, under mutual
TLS, a browser's PKCS#12 bundle, written owner-only to ``--bundle-out`` (default
``./<device id>.p12``) with its passphrase printed this once for the operator to type on the
device. ``register`` is the offline way in: the device's public key and certificate request,
copied off a device that can reach no enrolment listener, registered at the Hive Stand; the
device then waits like any other. ``deny`` refuses one. Each is a thin layer over the
loopback-only routes, called as the console device (``hivemind.cli.entrance.console``).

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Registered by
    ``hivemind.cli.entrance.group``. Calls into ``hivemind.cli.entrance.console`` and
    ``.render``, the Landing Board's models and ``hivemind.cli.landing``.

Key invariants:
    - Nothing is approved before the operator has seen the device's fingerprint.
    - An invite's code is printed once, here, and never logged; a bundle's passphrase likewise,
      and the bundle itself goes only to an owner-only file.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Enrolment is
      invite, key, pending, approval on loopback".
    - hivemind.entrance.routes.entrance.enrolments for the routes.
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from hivemind.cli.entrance.console import MANIFEST, YES, Stand, run_console
from hivemind.cli.entrance.render import device_lines, invite_lines, stamp
from hivemind.cli.landing import (
    PASSWORD_STDIN,
    CarriedOption,
    LandingError,
    SignedIn,
    carried_command,
    carried_flag,
    carried_path,
    carried_text,
    shown,
)
from hivemind.cli.stores import JsonOption
from hivemind.common.secrets import FILE_MODE
from hivemind.entrance.models import (
    ApprovalBody,
    ApprovedDeviceView,
    DenyBody,
    DeviceList,
    DeviceView,
    InviteRequest,
    InviteView,
    RedemptionView,
    RegistrationBody,
)
from waggle.clock import SystemClock

INTERACTIVE = CarriedOption(
    key="hivemind.cli.entrance.interactive",
    decls=("--interactive",),
    help="A person types the password at this device (a laptop's CLI): it may step up.",
    is_flag=True,
)
RENAME = CarriedOption(
    key="hivemind.cli.entrance.name",
    decls=("--name",),
    help="The device's name from now on (default: what the invite called it).",
)
CERTIFICATE_OUT = CarriedOption(
    key="hivemind.cli.entrance.certificate_out",
    decls=("--certificate-out",),
    help="Write the client certificate issued from the device's request to this file (for a "
    "device registered offline: it imports it with hive remote certificate import).",
)
BUNDLE_OUT = CarriedOption(
    key="hivemind.cli.entrance.bundle_out",
    decls=("--bundle-out",),
    help="Where to write a browser's PKCS#12 bundle, owner-only (default: ./<device id>.p12).",
)
# approve's own four options are parameters; the rest ride on the context.
ApproveCommand = carried_command(
    MANIFEST, PASSWORD_STDIN, INTERACTIVE, YES, RENAME, CERTIFICATE_OUT, BUNDLE_OUT
)
# A relative expiry: a count and a unit (minutes, hours, days, weeks), e.g. "90d".
_RELATIVE = re.compile(r"(\d{1,5})([mhdw])")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

DeviceArgument = Annotated[str, typer.Argument(help="The device's id (device_...).")]
DeviceOption = Annotated[
    str, typer.Option("--device", help="What you call the device, e.g. phone or laptop.")
]
SpendCapOption = Annotated[
    float,
    typer.Option("--spend-cap", min=0, help="The most it may spend per day, in USD (e.g. 5)."),
]
CapabilitiesOption = Annotated[
    list[str] | None,
    typer.Option(
        "--capabilities",
        help="What it may do, comma-separated or repeated (e.g. entrance:submit,observe); "
        "omitted, the device role's proposed set.",
    ),
]
ExpiresOption = Annotated[
    str | None,
    typer.Option(
        "--expires", help="When the approval lapses: 90d, 12h, 2w, or an ISO 8601 date or time."
    ),
]
NameOption = Annotated[str, typer.Option("--name", help="What the device is called.")]
PublicKeyOption = Annotated[
    str,
    typer.Option("--public-key", help="The device's Ed25519 public key, 64 hex characters."),
]
RequestOption = Annotated[
    Path,
    typer.Option("--csr", help="The device's certificate signing request, a PEM file."),
]

__all__ = [
    "BUNDLE_OUT",
    "CERTIFICATE_OUT",
    "INTERACTIVE",
    "RENAME",
    "ApproveCommand",
    "approve_command",
    "deny_command",
    "invite_command",
    "parse_capabilities",
    "parse_expiry",
    "pending_command",
    "register_command",
]


class NotConfirmedError(LandingError):
    """Raise when the operator did not say yes to an approval."""


def invite_command(ctx: typer.Context, device: DeviceOption, as_json: JsonOption = False) -> None:
    """Mint a single-use invite for a device: its code, link and QR code, shown once."""

    async def mint(board: SignedIn, stand: Stand) -> tuple[InviteView, str]:
        """Mint the invite on loopback."""
        body = InviteRequest(label=device)
        invite = await board.call("POST", "/v1/entrance/invites", body, InviteView)
        return invite, stand.manifest.hive.id

    invite, hive_id = run_console(ctx, "invite", mint)
    if as_json:
        shown_once = invite.model_dump(mode="json", exclude={"qr_svg"})
        typer.echo(json.dumps({**shown_once, "hive_id": hive_id}, indent=2))
        return
    for line in invite_lines(invite, hive_id, device):
        typer.echo(line)


def register_command(
    ctx: typer.Context, name: NameOption, public_key: PublicKeyOption, csr: RequestOption
) -> None:
    """Register a device offline from its public key and certificate request; it then waits."""
    try:
        request = csr.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise typer.BadParameter(f"{csr} is not a readable PEM request ({exc}).") from exc

    async def register(board: SignedIn, _stand: Stand) -> RedemptionView:
        """Register it on loopback."""
        key = public_key.strip().lower()
        body = RegistrationBody(name=name, public_key_hex=key, certificate_request=request)
        return await board.call("POST", "/v1/entrance/register", body, RedemptionView)

    registered = run_console(ctx, "register", register)
    typer.echo(f"Registered {registered.device_id} ({shown(name)}); it waits for approval.")
    typer.echo(f"  key fingerprint {registered.fingerprint}: compare it with the device's own.")
    typer.echo("Approve it and write its certificate for the device to import:")
    typer.echo(
        f"  hive entrance approve {registered.device_id} --spend-cap 5 --certificate-out "
        f"{registered.device_id}.crt"
    )


def pending_command(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """List the devices that redeemed an invite and wait for approval."""

    async def read(board: SignedIn, _stand: Stand) -> DeviceList:
        """Read the waiting devices."""
        return await board.call("GET", "/v1/entrance/pending", None, DeviceList)

    waiting = run_console(ctx, "pending", read)
    if as_json:
        typer.echo(waiting.model_dump_json(indent=2))
        return
    if not waiting.devices:
        typer.echo("No device is waiting for approval.")
    for device in waiting.devices:
        for line in device_lines(device):
            typer.echo(line)


def approve_command(
    ctx: typer.Context,
    device_id: DeviceArgument,
    spend_cap: SpendCapOption,
    capabilities: CapabilitiesOption = None,
    expires: ExpiresOption = None,
) -> None:
    """Approve a waiting device (loopback only), after showing its key fingerprint."""
    expiry = parse_expiry(expires, SystemClock().now()) if expires is not None else None
    granted = parse_capabilities(capabilities)
    confirmed = carried_flag(ctx, YES)
    bundle_path = carried_path(ctx, BUNDLE_OUT) or Path(f"{device_id}.p12")

    async def decide(board: SignedIn, _stand: Stand) -> ApprovedDeviceView:
        """Show the device, ask, then approve it with the operator's terms."""
        device = await _waiting(board, device_id)
        for line in device_lines(device):
            typer.echo(line)
        # A browser may get a bundle, sealed only once: it needs a place to go before it exists.
        if device.key_kind == "passkey":
            _writable(bundle_path)
        # The fingerprint is on screen; the operator compares it with the one the device shows.
        if not confirmed and not typer.confirm(f"Approve {device.id}?", default=False):
            raise NotConfirmedError(f"Device {device.id} was not approved.")
        body = ApprovalBody(
            name=carried_text(ctx, RENAME) or device.name,
            capabilities=granted,
            spend_cap_usd_per_day=spend_cap,
            expires_at=expiry,
            interactive=carried_flag(ctx, INTERACTIVE),
        )
        path = f"/v1/entrance/pending/{device.id}/approve"
        return await board.call("POST", path, body, ApprovedDeviceView)

    approved = run_console(ctx, "approve", decide)
    typer.echo(
        f"Approved {approved.id} as {shown(approved.name)}: {len(approved.capabilities)} "
        f"capabilities, {spend_cap:.2f} USD a day, interactive={approved.interactive}."
    )
    for line in _issued(approved, carried_path(ctx, CERTIFICATE_OUT), bundle_path):
        typer.echo(line)


def deny_command(
    ctx: typer.Context,
    device_id: DeviceArgument,
    reason: Annotated[str, typer.Option("--reason", help="Why, a sentence.")] = (
        "denied by the operator"
    ),
) -> None:
    """Deny a waiting device (loopback only)."""

    async def refuse(board: SignedIn, _stand: Stand) -> DeviceView:
        """Deny it on loopback."""
        path = f"/v1/entrance/pending/{device_id}/deny"
        return await board.call("POST", path, DenyBody(reason=reason), DeviceView)

    denied = run_console(ctx, "deny", refuse)
    typer.echo(f"Denied {denied.id} ({shown(denied.name)}).")


def parse_capabilities(values: list[str] | None) -> list[str] | None:
    """Flatten ``--capabilities`` values, comma-separated or repeated.

    Args:
        values: The option's values, or None when it was not given.

    Returns:
        Every capability named, in order, blanks dropped; None when none were named at all.
    """
    if values is None:
        return None
    return [part.strip() for value in values for part in value.split(",") if part.strip()]


def parse_expiry(text: str, now: datetime) -> datetime:
    """Read ``--expires``: a count and a unit (``90d``) from now, or an ISO 8601 date or time.

    Args:
        text: What the operator typed.
        now: The Hive Stand's clock.

    Returns:
        The expiry, timezone-aware (UTC when the text named no zone).

    Raises:
        typer.BadParameter: Neither form, or a moment already past.
    """
    relative = _RELATIVE.fullmatch(text.strip())
    if relative is not None:
        count, unit = int(relative.group(1)), relative.group(2)
        moment = now + timedelta(**{_UNITS[unit]: count})
    else:
        try:
            moment = datetime.fromisoformat(text.strip())
        except ValueError as exc:
            raise typer.BadParameter(f"{text!r} is neither 90d/12h/2w nor an ISO date.") from exc
        moment = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    if moment <= now:
        raise typer.BadParameter(f"{text!r} is not in the future.")
    return moment


def _issued(
    approved: ApprovedDeviceView, certificate_out: Path | None, bundle_out: Path
) -> list[str]:
    """Keep what the approval issued (a certificate, a bundle) and say where it went."""
    lines: list[str] = []
    if approved.certificate_serial is not None:
        lines.append(
            f"Client certificate: serial {approved.certificate_serial}, fingerprint "
            f"{approved.certificate_fingerprint}, until {stamp(approved.certificate_not_after)}."
        )
    if approved.bundle is not None:
        # A private key inside: owner-only, and the passphrase shown this once, never logged.
        _write_owner_only(bundle_out, base64.b64decode(approved.bundle.pkcs12_base64))
        lines.append(f"Wrote the device's certificate bundle to {bundle_out}; import it there.")
        lines.append(f"Its passphrase, shown this once: {approved.bundle.passphrase}")
    elif approved.certificate_pem is not None and certificate_out is not None:
        _write_owner_only(certificate_out, approved.certificate_pem.encode("ascii"))
        lines.append(
            f"Wrote the certificate to {certificate_out}; on the device: hive remote "
            f"certificate import {certificate_out}"
        )
    elif approved.certificate_pem is not None:
        lines.append("The device fetches it: hive remote certificate fetch.")
    return lines


def _writable(path: Path) -> Path:
    """Refuse a destination whose directory cannot be written, before anything is issued."""
    folder = path.parent if str(path.parent) else Path()
    if not folder.is_dir() or not os.access(folder, os.W_OK):
        raise typer.BadParameter(f"{folder} is not a directory this user can write to.")
    return path


def _write_owner_only(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` owner-only, whole or not at all."""
    handle, temporary = tempfile.mkstemp(dir=path.parent or None, prefix=f".{path.name}.")
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
    os.chmod(temporary, FILE_MODE)
    os.replace(temporary, path)


async def _waiting(board: SignedIn, device_id: str) -> DeviceView:
    """Return the waiting device ``device_id``, or refuse: only a pending device is approved."""
    waiting = await board.call("GET", "/v1/entrance/pending", None, DeviceList)
    for device in waiting.devices:
        if device.id == device_id:
            return device
    raise LandingError(
        f"Device {device_id} is not waiting for approval; hive entrance pending lists those "
        "that are."
    )
