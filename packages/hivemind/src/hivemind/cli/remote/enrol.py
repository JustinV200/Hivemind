"""Enrol this laptop with a Hive: mint its key, redeem the invite, and keep the profile.

``hive remote enrol`` is the device's side of ADR-0041's enrolment. The operator ran ``hive
entrance invite`` on the Hive Stand and read out (or pasted) the Entrance's URL and the single-use
code; the invite link itself (``<origin>/enrol#code=...``) is accepted in place of the URL and the
code. The Hive's id, which every signature names, is asked of the Entrance itself (``GET
/v1/enrol/hive``, over the same verified TLS the redemption then uses); a ``--hive`` (or a
``hive=`` in the link) is checked against that answer, and a disagreement refuses. The laptop
mints a fresh Ed25519 key, proves it holds it by signing ``hive-enrol-v1`` over the Hive id, the
code's hash and the key, sends a certificate signing request for the same key (the Hive signs its
mutual-TLS client certificate from it at approval, when it runs its own authority), and redeems
the invite; the Entrance answers with the key's fingerprint (checked against the one computed
here, so a mangled key is caught before anything is kept) and the Hive's public key, which the
profile pins. The device is PENDING until the operator approves it at the Hive Stand, comparing
the fingerprint printed here with the one ``hive entrance approve`` shows. Nothing is written
unless the redemption succeeded; a profile name already in use is refused rather than
overwritten, since its key is the only way back into that Hive. ``enrol_offline`` is the way in
for a laptop that can reach no enrolment listener (a Hive in ``lan`` or ``tunnel`` mode, where
nothing passes the remote listener without a certificate): it touches no network, mints the key
and writes the certificate request to a file the operator registers at the Hive Stand, and keeps
a profile whose device id the certificate the operator hands back will name.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. Called by ``hive remote enrol``
    (``hivemind.cli.remote.commands``). Calls into ``hivemind.cli.landing`` (the client, the
    transport), ``hivemind.cli.remote.profiles`` and ``hivemind.entrance`` (the code's form, the
    fingerprint, the description model).

Key invariants:
    - The private key is generated here, sent nowhere, and kept only in the secret store.
    - The invite code is never printed, logged or kept.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Enrolment is
      invite, key, pending, approval on loopback".
"""

from __future__ import annotations

import asyncio
import platform
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from hivemind.cli.landing import (
    EntranceAddress,
    LandingClient,
    LandingError,
    LandingProtocolError,
    LandingRefusedError,
    certificate_request,
    entrance_address,
    open_http,
    read_hive_id,
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
_NOT_FOUND = 404  # An Entrance from before GET /v1/enrol/hive answers its path this way.

__all__ = [
    "EnrolmentOrder",
    "InviteLink",
    "OfflineEnrolment",
    "enrol_device",
    "enrol_offline",
    "read_invite_link",
]


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


@dataclass(frozen=True, slots=True)
class OfflineEnrolment:
    """What an offline enrolment made, for the operator to register at the Hive Stand.

    Attributes:
        profile: The kept profile, without a device id until its certificate is imported.
        public_key_hex: The device key's public half, hex: ``--public-key`` at the Hive Stand.
        request_path: Where the certificate signing request was written: ``--csr`` there.
    """

    profile: RemoteProfile
    public_key_hex: str
    request_path: Path


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
    name = _free_name(store, order.profile)
    code = _code(order.code or link.code)
    named = order.hive_id or link.hive_id
    named_id = _hive_id(named) if named is not None else None
    address = entrance_address(order.link, order.ca_file)
    signer = Ed25519Signer.generate()
    request = certificate_request(signer, order.name)
    # Latency: two round trips to the Entrance, each bounded by the client's own timeout.
    async with open_http(address) as http:
        hive_id = await _served_hive(http, address, named_id)
        client = LandingClient(http, address, hive_id, clock)
        redemption = await client.redeem(code, signer, _description(order.name), request)
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


async def enrol_offline(
    store: ProfileStore, order: EnrolmentOrder, request_path: Path, clock: Clock
) -> OfflineEnrolment:
    """Mint the key and its certificate request for an operator to register; no network.

    Args:
        store: This user's remote profiles.
        order: What the user was told; the code is not needed (the Hive Stand mints its own).
        request_path: Where to write the certificate signing request.
        clock: Stamps the profile.

    Returns:
        The kept profile, the public key and where the request is.

    Raises:
        LandingError: The Hive id is missing or malformed, the URL is not an Entrance's, or the
            profile exists.
    """
    link = read_invite_link(order.link)
    name = _free_name(store, order.profile)
    hive_id = _hive_id(order.hive_id or link.hive_id)
    address = entrance_address(order.link, order.ca_file)
    signer = Ed25519Signer.generate()
    # The request is public (a key and a signature): written for the operator to carry across.
    # Latency: one small local write, off the event loop.
    await asyncio.to_thread(request_path.write_text, certificate_request(signer, order.name))
    profile = RemoteProfile(
        name=name,
        entrance_url=address.origin,
        ca_file=str(order.ca_file.resolve()) if order.ca_file is not None else None,
        hive_id=hive_id,
        device_name=order.name,
        fingerprint=key_fingerprint(signer.public_key_bytes),
        enrolled_at=clock.now(),
    )
    await store.save(profile, signer)
    return OfflineEnrolment(profile, signer.public_key_bytes.hex(), request_path)


def _free_name(store: ProfileStore, profile: str) -> str:
    """The profile name to enrol as, refused when it already holds a device."""
    name = check_profile_name(profile)
    if name in store.names():
        raise LandingError(
            f"Profile {name!r} already holds a device; forget it first (hive remote forget "
            f"{name}) or choose another with --profile."
        )
    return name


async def _served_hive(http: httpx.AsyncClient, address: EntranceAddress, named: str | None) -> str:
    """The Hive the Entrance says it serves; a named id must agree with it."""
    try:
        served = await read_hive_id(http, address)
    except LandingRefusedError as refusal:
        # An Entrance from before the route cannot say: only an id named here will do then.
        if refusal.status != _NOT_FOUND or named is None:
            raise
        return named
    if named is not None and named != served:
        raise LandingError(
            f"The Entrance at {address.origin} serves Hive {served}, not {named}; check the "
            "invite link or --hive."
        )
    return served


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
            "No Hive id: pass --hive (hive entrance invite prints it beside the code); offline, "
            "the Entrance cannot be asked."
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
