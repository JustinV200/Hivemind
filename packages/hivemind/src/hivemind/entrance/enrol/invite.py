"""Mint and cancel device invites: a single-use code, its link and QR codes, an INVITED record.

Enrolment starts on the Hive Stand's loopback listener (the machine the Queen, the orchestrator,
runs on; ADR-0033): ``hive entrance invite --device "phone"`` mints a 128-bit, single-use code and
shows it once, as grouped text and as a QR code of the Entrance's enrolment link with the code in
the URL fragment (a fragment never reaches a server log or a proxy). Only the code's SHA-256 is
stored. The device record is created INVITED with the label as its provisional name and the
invite's expiry (``invite_ttl_minutes``), and ``guard.entrance_invited`` is recorded with it. The
code is RFC 4648 base32 without padding in groups of four (``ABCD-EFGH-...-XY``, 26 characters,
unambiguous to read aloud); its hash is taken over that grouped form, which is also what a program
hashes for its enrolment signature, and ``canonical_invite_code`` restores it from a code a person
typed in lower case, without dashes or with spaces. ``cancel_invite`` withdraws an unredeemed one.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Called by the
    ``hive entrance invite`` command and the loopback-only invite route (later steps), and by
    ``hivemind.entrance.enrol.redeem`` for the code's hash. Calls into the Entrance tables
    through ``EnrolmentDeps``, ``hivemind.entrance.enrol.record`` and ``segno`` (the QR codes).

Key invariants:
    - The code exists only in the returned ``MintedInvite`` (whose ``repr`` hides it and every
      value carrying it): never in the tables, a trail event, a log line or an error message.
    - An invite is minted for a record that is INVITED, lives exactly ``invite_ttl`` and admits
      once (``hivemind.entrance.store``).

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the invite.
    - hivemind.entrance.enrol.redeem for how a device presents it.
"""

from __future__ import annotations

import base64
import io
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime

import segno

from hivemind.entrance.auth.canonical import sha256_hex
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.enrol.models import DeviceInvite, EnrolledDevice
from hivemind.entrance.enrol.record import (
    OPERATOR_ACTOR,
    Transition,
    apply_transition,
    iso,
    notify,
)
from hivemind.entrance.enrol.state import INVITED_TRAIL_KIND, DeviceStatus
from waggle.ids import DeviceId, new_device_id

INVITE_CODE_BYTES = 16  # 128 random bits (ADR-0033): unguessable in an invite's minutes of life.
INVITE_CODE_GROUP_CHARS = 4  # "ABCD-EFGH-...": groups a person compares and types at a glance.
INVITE_PATH = "/enrol"  # The Entrance page a device opens to enrol; the code rides the fragment.
INVITE_CANCELLED = "invite_cancelled"  # The reason cancel_invite records on the trail.
QR_ERROR_LEVEL = "m"  # 15% recovery: a camera reading a screen needs little; a denser QR is worse.
QR_SVG_SCALE = 4  # Pixels per module in the SVG; the Observation Hive scales it further in CSS.
_CODE_SEPARATOR = "-"  # Between groups of the code as shown.
_CODE_CHARS = 26  # 16 bytes in base32 without padding: ceil(128 / 5).
_BASE32_BLOCK = 8  # base32 pads to a multiple of 8 characters; stripped for display.
_COMPACT_CODE = re.compile(r"[A-Z2-7]{26}")  # RFC 4648 base32's alphabet, the code's length.

__all__ = [
    "INVITE_CANCELLED",
    "INVITE_CODE_BYTES",
    "INVITE_CODE_GROUP_CHARS",
    "INVITE_PATH",
    "QR_ERROR_LEVEL",
    "QR_SVG_SCALE",
    "InviteQr",
    "MintedInvite",
    "cancel_invite",
    "canonical_invite_code",
    "invite_code_hash",
    "invite_url",
    "mint_invite",
    "new_invite_code",
]


@dataclass(frozen=True, slots=True)
class InviteQr:
    """An invite link as a QR code, in the two forms the Hive Stand shows it.

    Attributes:
        terminal: Unicode block characters, for ``hive entrance invite`` in a terminal.
        svg: An inline SVG document, for the Observation Hive on loopback.
    """

    terminal: str = field(repr=False)
    svg: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class MintedInvite:
    """A freshly minted invite, shown once at the Hive Stand and never stored.

    Attributes:
        device_id: The INVITED record the invite admits.
        expires_at: When the invite stops admitting.
        code: The code in its grouped form, e.g. ``"ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ"``.
        url: The enrolment link, ``<base>/enrol#code=<code>``.
        qr: The link as a QR code, for a terminal and for a page.
    """

    device_id: DeviceId
    expires_at: datetime
    code: str = field(repr=False)
    url: str = field(repr=False)
    qr: InviteQr = field(repr=False)


def new_invite_code() -> str:
    """Mint a fresh invite code: 128 bits from the CSPRNG, grouped base32.

    Returns:
        The code in its grouped form.
    """
    raw = base64.b32encode(secrets.token_bytes(INVITE_CODE_BYTES)).decode("ascii")
    # base32 pads 16 bytes to 32 characters; the six "=" carry nothing, so they are not shown.
    return _grouped(raw.rstrip("="))


def canonical_invite_code(text: str) -> str:
    """Restore an invite code's grouped form from however a person typed it.

    Args:
        text: The code, in any case, with or without dashes or whitespace.

    Returns:
        The grouped, upper-case form the code was minted in.

    Raises:
        ValueError: ``text`` is not 26 base32 characters that encode 16 bytes exactly; the
            message never repeats the text.
    """
    # Whitespace, dashes and case are how people retype a code; none of them is part of it.
    compact = "".join(text.split()).replace(_CODE_SEPARATOR, "").upper()
    if _COMPACT_CODE.fullmatch(compact) is None:
        raise ValueError(f"An invite code is {_CODE_CHARS} base32 characters (A-Z, 2-7).")
    # 26 characters carry 130 bits for 128: a code whose spare bits are set is a typo, not a
    # second spelling of a real one, so it must not decode to the same bytes.
    padded = compact + "=" * (-len(compact) % _BASE32_BLOCK)
    if base64.b32encode(base64.b32decode(padded)).decode("ascii") != padded:
        raise ValueError("An invite code's last character is not one a real code ends with.")
    return _grouped(compact)


def invite_code_hash(code: str) -> str:
    """Return the SHA-256 an invite is stored and signed under, from however the code was typed.

    Args:
        code: The invite code, in any form ``canonical_invite_code`` accepts.

    Returns:
        The lowercase hex SHA-256 of the code's grouped form, as UTF-8.

    Raises:
        ValueError: ``code`` is not an invite code.
    """
    return sha256_hex(canonical_invite_code(code).encode("utf-8"))


def invite_url(base_url: str, code: str) -> str:
    """Build the enrolment link a device opens, the code in its fragment.

    Args:
        base_url: The Entrance's base URL (``EnrolmentRules.invite_base_url``).
        code: The invite code, grouped.

    Returns:
        ``<base_url>/enrol#code=<code>``; the code's alphabet and dashes need no escaping.
    """
    return f"{base_url.rstrip('/')}{INVITE_PATH}#code={code}"


async def mint_invite(deps: EnrolmentDeps, label: str, actor: str = OPERATOR_ACTOR) -> MintedInvite:
    """Mint a single-use invite for a new device record, INVITED under ``label``.

    Args:
        deps: The enrolment dependencies.
        label: What the operator calls the device, e.g. ``"phone"``; its name until approval.
        actor: Who minted it: ``"human"`` at the Hive Stand, or the console's device id when a
            loopback session mints it.

    Returns:
        The device id, the expiry, and the code with its link and QR codes, shown once.

    Raises:
        pydantic.ValidationError: ``label`` is empty, longer than a device name may be, or
            carries control or bidirectional characters; nothing was written.
    """
    records = deps.records
    now = records.clock.now()
    expires_at = now + deps.rules.invite_ttl
    code = new_invite_code()
    # Every record is built, and so validated, before the first write: a bad label leaves
    # nothing behind.
    device = EnrolledDevice(
        id=new_device_id(records.clock),
        name=label,
        status=DeviceStatus.INVITED,
        created_at=now,
        expires_at=expires_at,
    )
    invite = DeviceInvite(
        code_hash=invite_code_hash(code),
        device_id=device.id,
        label=label,
        created_at=now,
        expires_at=expires_at,
    )
    payload = {"label": label, "expires_at": iso(expires_at)}
    event = records.identity.event(records.clock, INVITED_TRAIL_KIND, device.id, payload, actor)
    # Latency: two local writes. The record and its event commit together; should the process
    # die before the invite row, the record admits nothing and simply expires on schedule.
    await records.store.put_device(device, event)
    await records.store.put_invite(invite)
    await notify(deps, device.id, event)
    return _minted(deps, device.id, expires_at, code)


async def cancel_invite(deps: EnrolmentDeps, device_id: DeviceId, actor: str) -> EnrolledDevice:
    """Withdraw an unredeemed invite: its device moves INVITED to REVOKED.

    Args:
        deps: The enrolment dependencies.
        device_id: The INVITED device whose invite is withdrawn.
        actor: Who withdrew it: the console's device id, or ``"human"`` at the Hive Stand.

    Returns:
        The device as stored, REVOKED; its invite now admits nothing.

    Raises:
        DeviceNotFoundError: No such device.
        DeviceStatusConflictError: The device is no longer INVITED (it redeemed, or expired).
    """
    transition = Transition(
        device_id, DeviceStatus.INVITED, DeviceStatus.REVOKED, actor, {"reason": INVITE_CANCELLED}
    )
    return await apply_transition(deps, transition)


def _minted(
    deps: EnrolmentDeps, device_id: DeviceId, expires_at: datetime, code: str
) -> MintedInvite:
    """Assemble what the operator is shown: the code, its link and both QR renderings."""
    url = invite_url(deps.rules.invite_base_url, code)
    # A regular QR code, never Micro QR, which phone cameras do not read reliably.
    qr = segno.make_qr(url, error=QR_ERROR_LEVEL)
    terminal = io.StringIO()
    qr.terminal(out=terminal, compact=True)
    rendered = InviteQr(terminal=terminal.getvalue(), svg=qr.svg_inline(scale=QR_SVG_SCALE))
    return MintedInvite(device_id=device_id, expires_at=expires_at, code=code, url=url, qr=rendered)


def _grouped(compact: str) -> str:
    """Split a compact code into dash-joined groups of ``INVITE_CODE_GROUP_CHARS``."""
    step = INVITE_CODE_GROUP_CHARS
    return _CODE_SEPARATOR.join(
        compact[start : start + step] for start in range(0, len(compact), step)
    )
