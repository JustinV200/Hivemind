"""Issue a device's mutual-TLS client certificate at approval, and remember it on its record.

Under mutual TLS (``lan`` and ``tunnel`` always, ``vpn`` when the operator turns it on) the remote
listener admits only a device holding a client certificate from the Hive's own authority
(ADR-0041), and the device receives it when it is approved: signed from the certificate signing
request a program sent with its key (at redemption, or registered offline by the operator), or,
for a browser, which cannot make a request, sealed with a fresh key into a PKCS#12 bundle the
operator imports on the device. ``DeviceCertifier`` does the issuing for the enrolment flows: it
holds the authority when the Hive runs one (every remote mode), and it seals bundles only when the
listener demands certificates, since a bundle is a file and a passphrase the operator has to carry
to the device. A certificate from a request costs nothing to hold and is public, so one is issued
in every remote mode: a device approved over the VPN keeps working if the operator later turns
mutual TLS on. ``CertificateRecord`` is what the device's record keeps of it: the serial the
revocation list names, the fingerprint an operator compares, the expiry, the certificate itself
(public, for the device to fetch), and when the Hive stopped honouring it. ``IssuedBundle`` is a
bundle handed out once, never stored.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. The certifier
    is built by the Entrance's composition root and carried in ``EnrolmentSeams``; approval
    (``decisions``) issues through it, redemption and offline registration (``redeem``) check a
    request with it; ``CertificateRecord`` is a field of ``EnrolledDevice``. Calls into
    ``hivemind.entrance.expose.tls`` (the authority and the issuing) only.

Key invariants:
    - No private key, passphrase or bundle is kept here or appears in a ``repr``; a bundle leaves
      in the approval's answer to the operator and is never written to the Entrance tables.
    - A certifier without an authority issues nothing and seals nothing.
    - A request is checked (parse, signature, key kind) the same way whether it is only being
      received or is being signed.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "receives its
      client certificate at approval".
    - hivemind.entrance.expose.tls.issue for the certificates themselves.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.expose.tls import (
    MAX_CSR_BYTES,
    DeviceCertificate,
    HiveAuthority,
    check_certificate_request,
    issue_client_certificate,
    issue_pkcs12,
)
from waggle.ids import DeviceId
from waggle.messages.base import UtcDatetime

MAX_CERTIFICATE_REQUEST_CHARS = MAX_CSR_BYTES  # A request is ASCII PEM: characters are bytes.
MAX_CERTIFICATE_PEM_CHARS = 8192  # A leaf certificate is under 2 KiB even with an RSA-4096 key.
PASSPHRASE_GROUPS = 5  # A bundle's passphrase: five groups of four base32 characters, 100 bits.
_GROUP_CHARS = 4  # One group, read aloud or typed on a phone at a time.
_PASSPHRASE_BYTES = 13  # 104 random bits, base32-encoded; the first 20 characters are kept.

__all__ = [
    "MAX_CERTIFICATE_PEM_CHARS",
    "MAX_CERTIFICATE_REQUEST_CHARS",
    "PASSPHRASE_GROUPS",
    "CertificateRecord",
    "DeviceCertifier",
    "IssuedBundle",
    "new_bundle_passphrase",
    "withdrawn",
]


class CertificateRecord(BaseModel):
    """What a device's record keeps of its client certificate: everything public about it.

    Crosses into the Entrance tables inside the device's JSON body, and out through the device
    views (the serial, fingerprint and expiry) and the device's own certificate route (the PEM).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    serial: str = Field(
        pattern=r"^[0-9a-f]{1,40}$",
        description="Its serial number, lowercase hex: what the revocation list names.",
    )
    fingerprint: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 of the certificate (DER), lowercase hex: what an operator compares.",
    )
    not_after: UtcDatetime = Field(description="When it expires.")
    pem: str = Field(
        max_length=MAX_CERTIFICATE_PEM_CHARS,
        description="The certificate, PEM; public, and what the device fetches.",
    )
    revoked_at: UtcDatetime | None = Field(
        default=None,
        description="When the Hive stopped honouring it (its device was revoked or expired); "
        "None while it stands.",
    )


@dataclass(frozen=True, slots=True)
class IssuedBundle:
    """A browser's PKCS#12 bundle, handed to the operator once and never stored.

    Attributes:
        pkcs12: The sealed key and certificate; opaque without the passphrase.
        passphrase: What seals it; shown to the operator once, for the import on the device.
    """

    pkcs12: bytes = field(repr=False)
    passphrase: SecretStr


class DeviceCertifier:
    """Issue devices' client certificates from the Hive's own authority, when the Hive runs one."""

    def __init__(self, authority: HiveAuthority | None = None, *, bundles: bool = False) -> None:
        """Hold the authority, and whether browsers get bundles (only under mutual TLS).

        Args:
            authority: The Hive's certificate authority; None (loopback only) issues nothing.
            bundles: Seal a PKCS#12 bundle for a passkey device at approval; only meaningful
                with an authority, and only worth the operator's trouble under mutual TLS.
        """
        self._authority = authority
        self._bundles = bundles and authority is not None

    @property
    def issues(self) -> bool:
        """Whether approval issues a certificate from a device's request."""
        return self._authority is not None

    @property
    def bundles(self) -> bool:
        """Whether approval seals a PKCS#12 bundle for a passkey device."""
        return self._bundles

    def check_request(self, request_pem: str, device_id: str) -> None:
        """Check a certificate signing request as issuing will, whether or not this Hive issues.

        Args:
            request_pem: The PEM request, as received.
            device_id: The device it came with, or its name before it has an id (for a refusal).

        Raises:
            CertificateRequestError: It does not parse, verify, or carry an acceptable key.
        """
        check_certificate_request(request_pem.encode("utf-8"), device_id)

    def certify(self, device_id: DeviceId, request_pem: str, now: datetime) -> CertificateRecord:
        """Sign the device's certificate from its request.

        Args:
            device_id: The device being approved.
            request_pem: The request it sent (already checked when it arrived).
            now: The issuing time.

        Returns:
            The record of the certificate.

        Raises:
            InvariantViolationError: This certifier has no authority (check ``issues`` first).
            CertificateRequestError: The request does not verify.
            CertificateAuthorityError: The authority is not current.
        """
        issued = issue_client_certificate(
            self._require_authority(), request_pem.encode("utf-8"), device_id, now
        )
        return _record(issued)

    def bundle(self, device_id: DeviceId, now: datetime) -> tuple[CertificateRecord, IssuedBundle]:
        """Seal a fresh key and its certificate into a PKCS#12 bundle for a browser.

        Args:
            device_id: The passkey device being approved.
            now: The issuing time.

        Returns:
            The record of the certificate inside, and the bundle with its passphrase.

        Raises:
            InvariantViolationError: This certifier seals no bundles (check ``bundles`` first).
            CertificateAuthorityError: The authority is not current.
        """
        if not self._bundles:
            raise InvariantViolationError("This Hive seals no certificate bundles.")
        passphrase = new_bundle_passphrase()
        sealed = issue_pkcs12(self._require_authority(), device_id, now, passphrase)
        return _record(sealed.certificate), IssuedBundle(sealed.pkcs12, passphrase)

    def _require_authority(self) -> HiveAuthority:
        """The authority, or the invariant a caller broke by issuing without one."""
        if self._authority is None:
            raise InvariantViolationError("This Hive runs no certificate authority to issue from.")
        return self._authority


def withdrawn(record: CertificateRecord, at: datetime) -> CertificateRecord:
    """Mark a certificate no longer honoured from ``at``: its device was revoked or expired.

    Args:
        record: The device's certificate.
        at: When its device left its approval.

    Returns:
        The record with ``revoked_at`` set; the one given when it already had one (the first
        withdrawal is the one the revocation list dates).
    """
    if record.revoked_at is not None:
        return record
    return CertificateRecord.model_validate({**dict(record), "revoked_at": at})


def new_bundle_passphrase() -> SecretStr:
    """A fresh bundle passphrase: 100 random bits as five groups of four base32 characters.

    Returns:
        E.g. ``ABCD-EFGH-IJKL-MNOP-QRST``; grouped so an operator can type it on a phone.
    """
    letters = base64.b32encode(os.urandom(_PASSPHRASE_BYTES)).decode("ascii")
    groups = [letters[i * _GROUP_CHARS : (i + 1) * _GROUP_CHARS] for i in range(PASSPHRASE_GROUPS)]
    return SecretStr("-".join(groups))


def _record(issued: DeviceCertificate) -> CertificateRecord:
    """Describe an issued certificate for the device's record."""
    return CertificateRecord(
        serial=format(issued.serial, "x"),
        fingerprint=issued.fingerprint,
        not_after=issued.not_after,
        pem=issued.pem.decode("ascii"),
    )
