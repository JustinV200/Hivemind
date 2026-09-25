"""Render what ``hive entrance`` shows: devices, an invite, every foreign string escaped.

Everything a device says about itself (its name, platform and User-Agent) reached the Hive over an
unauthenticated route from a device nobody trusts yet, and the Hive's own text can quote a model's
words; on a terminal, a control character, an escape sequence or a bidirectional override in such a
string could rewrite what the operator sees at the one moment it matters, the approval (ADR-0041:
"every approval surface ... escapes every string the device supplied"). ``hivemind.cli.landing.
shown`` is that escape, applied here to every such string before it is printed. ``device_lines`` is
the approval view (the key's fingerprint, the passkey backup flags, the self-description),
``device_row`` one line of a table, and ``invite_lines`` the invite as the Hive Stand prints it:
the grouped code, the link, the Hive's id, the command a program or laptop enrols with, and the
link as a terminal QR code (segno).

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.entrance``. Used by the ``hive entrance``
    commands. Calls into ``segno``, ``hivemind.cli.landing.shown`` and the Landing Board's view
    models only.

Key invariants:
    - No string a device or a model wrote is printed without ``shown``.
    - Nothing here prints a key, a token or a password; a fingerprint is the most a key shows.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Every approval
      surface shows the key fingerprint and the backup flags and escapes every string the device
      supplied."
"""

from __future__ import annotations

import io
import shlex
from collections.abc import Sequence
from datetime import datetime
from urllib.parse import urlsplit

import segno

from hivemind.cli.landing import shown
from hivemind.entrance.models import DeviceView, InviteView

QR_ERROR_LEVEL = "m"  # The Entrance's own choice for an invite's QR (enrol/invite.py).

__all__ = ["device_lines", "device_row", "devices_table", "invite_lines", "stamp"]


def stamp(moment: datetime | None) -> str:
    """Render a time for a table: ISO 8601 to the second, or ``never``."""
    return moment.isoformat(timespec="seconds") if moment is not None else "never"


def device_row(device: DeviceView) -> str:
    """One line of a device table: id, status, name, key kind, fingerprint, cap.

    Args:
        device: The device as the Landing Board shows it.

    Returns:
        The line, every device-supplied field escaped.
    """
    cap = "none" if device.spend_cap_usd_per_day is None else f"{device.spend_cap_usd_per_day:.2f}"
    return "  ".join(
        (
            str(device.id),
            device.status.value.ljust(8),
            shown(device.name, 32).ljust(20),
            (device.key_kind or "-").ljust(7),
            device.fingerprint or "-",
            f"cap/day {cap}",
        )
    )


def device_lines(device: DeviceView) -> list[str]:
    """What the operator weighs before approving: the key, how it is kept, what it says it is.

    Args:
        device: The device waiting (or any device).

    Returns:
        One line per fact, every device-supplied string escaped.
    """
    description = device.description
    lines = [
        f"Device {device.id} ({device.status.value}), invited as {shown(device.name)}",
        f"  key: {device.key_kind or '-'}, fingerprint {device.fingerprint or '-'}",
        f"  passkey backup: eligible={device.backup_eligible}, synced={device.backup_state}",
    ]
    if description is not None:
        lines.append(f"  says it is: {shown(description.name)}")
        lines.append(f"  platform: {shown(description.platform)}")
        lines.append(f"  user agent: {shown(description.user_agent)}")
    lines.append(f"  asked at: {stamp(device.created_at)}; expires: {stamp(device.expires_at)}")
    if device.certificate_serial is not None:
        lines.append(
            f"  client certificate: serial {device.certificate_serial}, until "
            f"{stamp(device.certificate_not_after)}"
        )
    elif device.certificate_requested:
        lines.append("  client certificate: requested (signed at approval under a Hive authority)")
    return lines


def invite_lines(invite: InviteView, hive_id: str, device: str) -> list[str]:
    """The invite as the Hive Stand prints it: code, link, Hive id, enrol command, QR code.

    Args:
        invite: The invite the Entrance minted, shown once.
        hive_id: The Hive a program's enrolment signature names (it learns it from here).
        device: What the operator called the device.

    Returns:
        The lines, the QR code last.
    """
    origin = _origin(invite.url)
    # Quoted for a shell: a device label may hold spaces ("Ada's laptop").
    command = (
        f"hive remote enrol {origin} --hive {hive_id} --code {invite.code} "
        f"--name {shlex.quote(shown(device, 40))}"
    )
    return [
        f"Invite for {shown(device)} (device {invite.device_id}), single use, valid until "
        f"{stamp(invite.expires_at)}.",
        f"  Code: {invite.code}",
        f"  Link: {invite.url}",
        f"  Hive: {hive_id}",
        "A phone opens the link or scans the code below; a laptop or program runs:",
        f"  {command}",
        *_qr(invite.url),
    ]


def devices_table(devices: Sequence[DeviceView]) -> list[str]:
    """A table of devices, or a line saying there are none.

    Args:
        devices: The devices to list.

    Returns:
        The lines.
    """
    if not devices:
        return ["(no devices)"]
    return [device_row(device) for device in devices]


def _qr(url: str) -> list[str]:
    """The link as a terminal QR code: Unicode half blocks, two rows per line."""
    rendered = io.StringIO()
    segno.make_qr(url, error=QR_ERROR_LEVEL).terminal(out=rendered, compact=True)
    return rendered.getvalue().rstrip("\n").split("\n")


def _origin(url: str) -> str:
    """The Entrance's origin, from an invite link."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"
