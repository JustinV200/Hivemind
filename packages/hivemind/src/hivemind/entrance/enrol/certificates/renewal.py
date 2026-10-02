"""Give an approved device a fresh client certificate: a renewal by its key, a re-issue on loopback.

A device's certificate lasts ``CLIENT_CERT_VALIDITY`` (90 days), and a device approved while the
Hive ran loopback only was issued none, so without these two flows both would mean enrolling again
(ADR-0058). ``renew_certificate`` is the enrolled program's own way: logged in, holding a current
certificate within ``RENEWAL_WINDOW`` of its end, it sends a certificate signing request for the
very key it enrolled and logs in with (a session alone, without that key, gets nothing), and gets a
certificate for the same device id. ``reissue_certificate`` is the operator's, on loopback: for a
device holding no certificate (approved loopback-only), or one that lapsed, from the device's
request (the one it sent at redemption when none is given) or, for a browser under mutual TLS, as
a sealed bundle answered once. Either way the certificate it replaces is withdrawn in the same step
and kept in ``superseded_certificates`` until it expires, so the revocation list still names it:
nothing a device stops using stays honoured, and a later revocation of the device still reaches
every certificate it ever held. The step is one ``guard.entrance_certificate_issued`` trail event
with the device's new record, and every other device is told.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol.certificates``.
    Called by the devices routes (``hivemind.entrance.routes.devices``). Calls into
    ``.issuing``, the enrolment records (``record``, ``deps``, ``models``, ``state``),
    ``decisions`` (the ``Approval`` it answers with), ``CertificateChange`` and
    ``hivemind.entrance.errors``.

Key invariants:
    - Only an APPROVED device gets a new certificate, always for its own device id; a renewal
      only for its enrolled Ed25519 key, and only while its current one is unwithdrawn and
      within ``RENEWAL_WINDOW`` of its end.
    - A replaced, unexpired certificate is withdrawn in the same atomic step as the new one is
      recorded, and stays listed until it expires; two racing renewals cannot both win.
    - A bundle is returned to the caller once and never stored.

See Also:
    - docs/adr/0058-device-certificates-are-renewed-by-their-key-and-re-issued-on-loopback.md.
    - hivemind.entrance.runtime.tls for the revocation list the withdrawn certificates join.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from pydantic import JsonValue

from hivemind.entrance.auth.canonical import b64url_decode
from hivemind.entrance.auth.keys import KeyKind
from hivemind.entrance.enrol.certificates.issuing import (
    MAX_SUPERSEDED_CERTIFICATES,
    CertificateChange,
    CertificateRecord,
    IssuedBundle,
    withdrawn,
)
from hivemind.entrance.enrol.decisions import Approval
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.record import iso, notify
from hivemind.entrance.enrol.state import CERTIFICATE_TRAIL_KIND, DeviceStatus
from hivemind.entrance.errors import CertificateIssueRefusedError, DeviceStatusConflictError
from waggle.ids import DeviceId

# How close to its end a certificate may be renewed: a third of its 90 days, so a device that
# renews on time never runs on a certificate about to lapse, and holds at most one superseded.
RENEWAL_WINDOW = timedelta(days=30)

__all__ = [
    "RENEWAL_WINDOW",
    "IssueHow",
    "Reissue",
    "reissue_certificate",
    "renew_certificate",
]


class IssueHow(StrEnum):
    """How a certificate came after the approval's: the event's ``how``."""

    RENEWED = "renewed"  # The device renewed its own, by its key.
    REISSUED = "reissued"  # The operator re-issued one on loopback.


@dataclass(frozen=True, slots=True)
class Reissue:
    """What the operator asks of a re-issue.

    Attributes:
        actor: Who asks: the console's device id, or ``"human"`` at the Hive Stand.
        request_pem: The device's certificate signing request; None to use the one it sent at
            redemption (or, for a browser, to seal a bundle).
    """

    actor: str
    request_pem: str | None = None


async def renew_certificate(
    deps: EnrolmentDeps, device_id: DeviceId, request_pem: str
) -> EnrolledDevice:
    """Renew an approved program's certificate from a request for its own enrolled key.

    Args:
        deps: The enrolment dependencies.
        device_id: The calling device (its session proved it).
        request_pem: A certificate signing request for the device's enrolled Ed25519 key.

    Returns:
        The device as stored, with its new certificate.

    Raises:
        CertificateIssueRefusedError: No authority, a browser, no current certificate, not due
            yet, or another renewal replaced it first.
        CertificateRequestError: The request does not check, or certifies another key.
        DeviceStatusConflictError: The device is not APPROVED.
    """
    records = deps.records
    # Latency: one local primary-key read.
    device = await records.store.get_device(device_id)
    now = records.clock.now()
    _check_renewable(device, deps.seams.certifier.issues, now)
    assert device.public_key is not None  # noqa: S101 - a program's key, checked just above.
    holder = b64url_decode(device.public_key)
    issued = deps.seams.certifier.certify(device.id, request_pem, now, holder=holder)
    return await _replace(deps, device, issued, IssueHow.RENEWED, str(device.id))


async def reissue_certificate(
    deps: EnrolmentDeps, device_id: DeviceId, reissue: Reissue
) -> Approval:
    """Re-issue an approved device's certificate on the operator's word (loopback only).

    Args:
        deps: The enrolment dependencies.
        device_id: The APPROVED device.
        reissue: Who asks, and the request to certify (or None).

    Returns:
        The device as stored with its new certificate, and a browser's bundle when one was
        sealed (for the operator, this once).

    Raises:
        CertificateIssueRefusedError: No authority, no request to certify, or a browser while
            the Hive seals no bundles.
        CertificateRequestError: The request does not check, or certifies another key.
        DeviceStatusConflictError: The device is not APPROVED.
    """
    records = deps.records
    # Latency: one local primary-key read.
    device = await records.store.get_device(device_id)
    if device.status is not DeviceStatus.APPROVED:
        raise DeviceStatusConflictError(device.id, DeviceStatus.APPROVED, device.status)
    now = records.clock.now()
    issued, bundle = await _reissued(deps, device, reissue.request_pem, now)
    updated = await _replace(deps, device, issued, IssueHow.REISSUED, reissue.actor)
    return Approval(updated, bundle)


def _check_renewable(device: EnrolledDevice, issues: bool, now: datetime) -> None:
    """Refuse a renewal the device may not make now; say what it can do instead."""
    current = device.certificate
    if not issues:
        reason = "this Hive runs no certificate authority"
    elif device.key_kind is not KeyKind.ED25519:
        reason = "a browser's certificate is re-issued at the Hive Stand"
    elif current is None or current.revoked_at is not None or current.not_after <= now:
        reason = "it holds no current certificate; the operator re-issues one on loopback"
    elif current.not_after - now > RENEWAL_WINDOW:
        due = (current.not_after - RENEWAL_WINDOW).isoformat(timespec="seconds")
        reason = f"its certificate is not due for renewal until {due}"
    else:
        return
    raise CertificateIssueRefusedError(device.id, reason)


async def _reissued(
    deps: EnrolmentDeps, device: EnrolledDevice, request_pem: str | None, now: datetime
) -> tuple[CertificateRecord, IssuedBundle | None]:
    """Issue the re-issue's certificate: from a request for the device's key, or as a bundle."""
    certifier = deps.seams.certifier
    if not certifier.issues:
        raise CertificateIssueRefusedError(device.id, "this Hive runs no certificate authority")
    if device.key_kind is KeyKind.ED25519 and device.public_key is not None:
        request = request_pem if request_pem is not None else device.certificate_request
        if request is None:
            raise CertificateIssueRefusedError(
                device.id, "it sent no certificate request; give the one it makes for its key"
            )
        holder = b64url_decode(device.public_key)
        return certifier.certify(device.id, request, now, holder=holder), None
    if device.key_kind is KeyKind.PASSKEY and certifier.bundles and request_pem is None:
        # Latency: about 0.1 s of key generation and PBKDF2 sealing, off the event loop.
        return await asyncio.to_thread(certifier.bundle, device.id, now)
    raise CertificateIssueRefusedError(
        device.id, "a browser is given a sealed bundle, and only while mutual TLS is on"
    )


async def _replace(
    deps: EnrolmentDeps,
    device: EnrolledDevice,
    issued: CertificateRecord,
    how: IssueHow,
    actor: str,
) -> EnrolledDevice:
    """Record ``issued`` as the device's certificate, withdrawing the one it replaces."""
    records = deps.records
    old = device.certificate
    payload: dict[str, JsonValue] = {
        "how": how.value,
        "fingerprint": device.fingerprint,
        # The trail names certificates by serial and fingerprint, never their bytes.
        "certificate_serial": issued.serial,
        "certificate_fingerprint": issued.fingerprint,
        "certificate_not_after": iso(issued.not_after),
        "superseded_serial": old.serial if old is not None else None,
    }
    event = records.identity.event(records.clock, CERTIFICATE_TRAIL_KIND, device.id, payload, actor)
    superseded = _superseded(device, records.clock.now())
    change = CertificateChange(issued, superseded, old.serial if old is not None else None)
    # Latency: one local transaction writing the record and its event together.
    updated = await records.store.update_device_certificate(device.id, event, change)
    await notify(deps, device.id, event)
    return updated


def _superseded(device: EnrolledDevice, now: datetime) -> tuple[CertificateRecord, ...]:
    """The device's superseded list once its current certificate joins it, withdrawn now."""
    # An expired certificate no handshake accepts needs no list entry; every other one does.
    kept = tuple(old for old in device.superseded_certificates if old.not_after > now)
    current = device.certificate
    if current is not None and current.not_after > now:
        kept = (*kept, withdrawn(current, now))
    if len(kept) > MAX_SUPERSEDED_CERTIFICATES:
        raise CertificateIssueRefusedError(
            device.id,
            f"{MAX_SUPERSEDED_CERTIFICATES} replaced certificates have not expired yet; revoke "
            "the device and enrol it again",
        )
    return kept
