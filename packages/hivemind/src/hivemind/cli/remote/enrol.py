"""Enrol this laptop with a Hive: mint its key, redeem the invite, and keep the profile.

``hive remote enrol`` is the device's side of ADR-0033's enrolment. The operator ran ``hive
entrance invite`` on the Hive Stand and read out (or pasted) the Entrance's URL, the single-use
code and the Hive's id; the invite link itself (``<origin>/enrol#code=...``) is accepted in place
of the URL and the code, and a ``hive=`` beside the code in its fragment in place of ``--hive``.
The laptop mints a fresh Ed25519 key, proves it holds it by signing ``hive-enrol-v1`` over the
Hive id, the code's hash and the key, and redeems the invite; the Entrance answers with the key's
fingerprint (checked against the one computed here, so a mangled key is caught before anything is
kept) and the Hive's public key, which the profile pins. The device is PENDING until the operator
approves it at the Hive Stand, comparing the fingerprint printed here with the one ``hive entrance
approve`` shows. Nothing is written unless the redemption succeeded; a profile name already in use
is refused rather than overwritten, since its key is the only way back into that Hive.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. Called by ``hive remote enrol``
    (``hivemind.cli.remote.commands``). Calls into ``hivemind.cli.landing`` (the client, the
    transport), ``hivemind.cli.remote.profiles`` and ``hivemind.entrance`` (the code's form, the
    fingerprint, the description model).

Key invariants:
    - The private key is generated here, sent nowhere, and kept only in the secret store.
    - The invite code is never printed, logged or kept.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Enrolment is
      invite, key, pending, approval on loopback".
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from hivemind.cli.landing import (
    LandingClient,
    LandingError,
    LandingProtocolError,
    entrance_address,
    open_http,
)
from hivemind.cli.remote.profiles import ProfileStore, RemoteProfile, check_profile_name
from hivemind.cli.version import collect_version_info
from hivemind.entrance.auth import key_fingerprint
from hivemind.entrance.enrol import DeviceDescription, canonical_invite_code
from waggle.clock import Clock
from waggle.errors import InvalidIdError
from waggle.ids import IdKind, parse_id
from waggle.signing import Ed25519Signer

MAX_PLATFORM_CHARS = 64  # The Entrance's own bound on a device's platform string.

__all__ = ["EnrolmentOrder", "InviteLink", "enrol_device", "read_invite_link"]


@dataclass(frozen=True, slots=True)
class InviteLink:
    """What an Entrance URL or invite link says: the origin, and the code and Hive id if given.

    Attributes:
        url: The URL as given; its origin is taken when the address is built.
        code: The ``code=`` in the fragment, or None.
        hive_id: The ``hive=`` in the fragment, or None.
    """

    url: str
    code: str | None
    hive_id: str | None


@dataclass(frozen=True, slots=True)
class EnrolmentOrder:
    """Everything ``hive remote enrol`` was told.

    Attributes:
        link: The Entrance's URL, or the invite link.
        code: The invite code (None when the link carries it).
        hive_id: The Hive's id (None when the link carries it).
        name: What this laptop calls itself; the operator may rename it at approval.
        profile: The profile to keep it as.
        ca_file: The only CA the Entrance's TLS may chain to, or None for the system's.
    """

    link: str
    code: str | None
    hive_id: str | None
    name: str
    profile: str
    ca_file: Path | None = None


def read_invite_link(url: str) -> InviteLink:
    """Read an Entrance URL or invite link, taking the code and Hive id from its fragment.

    Args:
        url: ``https://hive.example.ts.net:8711`` or ``.../enrol#code=ABCD-...&hive=hive_...``.

    Returns:
        What it says.
    """
    fragment = parse_qs(urlsplit(url.strip()).fragment)
    code = fragment.get("code", [None])[0]
    hive_id = fragment.get("hive", [None])[0]
    return InviteLink(url=url, code=code, hive_id=hive_id)


async def enrol_device(store: ProfileStore, order: EnrolmentOrder, clock: Clock) -> RemoteProfile:
    """Redeem the invite with a fresh key and keep the profile; the device is then PENDING.

    Args:
        store: This user's remote profiles.
        order: What the user was told.
        clock: Stamps the enrolment.

    Returns:
        The kept profile.

    Raises:
        LandingError: A value is missing or malformed, the profile exists, or the Entrance
            refused (``LandingRefusedError``) or did not answer.
    """
    link = read_invite_link(order.link)
    name = check_profile_name(order.profile)
    if name in store.names():
        raise LandingError(
            f"Profile {name!r} already holds a device; forget it first (hive remote forget "
            f"{name}) or choose another with --profile."
        )
    code, hive_id = _code(order.code or link.code), _hive_id(order.hive_id or link.hive_id)
    address = entrance_address(order.link, order.ca_file)
    signer = Ed25519Signer.generate()
    # Latency: one round trip to the Entrance, bounded by the client's own timeout.
    async with open_http(address) as http:
        client = LandingClient(http, address, hive_id, clock)
        redemption = await client.redeem(code, signer, _description(order.name))
    # The Entrance fingerprints the key it stored; a mismatch means it is not the key sent.
    if redemption.fingerprint != key_fingerprint(signer.public_key_bytes):
        raise LandingProtocolError("The Entrance recorded a different key than this device sent.")
    profile = RemoteProfile(
        name=name,
        entrance_url=address.origin,
        ca_file=str(order.ca_file.resolve()) if order.ca_file is not None else None,
        hive_id=hive_id,
        device_id=redemption.device_id,
        device_name=order.name,
        fingerprint=redemption.fingerprint,
        hive_public_key_hex=redemption.hive_public_key_hex,
        enrolled_at=clock.now(),
    )
    await store.save(profile, signer)
    return profile


def _code(code: str | None) -> str:
    """The invite code in its grouped form; refused without repeating it."""
    if code is None:
        raise LandingError("No invite code: pass --code, or the invite link with #code=.")
    try:
        return canonical_invite_code(code)
    except ValueError as exc:
        raise LandingError(f"The invite code is not one: {exc}") from exc


def _hive_id(hive_id: str | None) -> str:
    """The Hive's id, checked; hive entrance invite prints it beside the code."""
    if hive_id is None:
        raise LandingError(
            "No Hive id: pass --hive (hive entrance invite prints it beside the code)."
        )
    try:
        return parse_id(hive_id, IdKind.HIVE)
    except InvalidIdError as exc:
        raise LandingError(f"{hive_id!r} is not a Hive id (hive_...).") from exc


def _description(name: str) -> DeviceDescription:
    """What this laptop says about itself: its name, its system, the CLI's version."""
    system = f"{platform.system()} {platform.release()}".strip()[:MAX_PLATFORM_CHARS]
    agent = f"hive-cli/{collect_version_info().hivemind_version}"
    return DeviceDescription(name=name, platform=system, user_agent=agent)
