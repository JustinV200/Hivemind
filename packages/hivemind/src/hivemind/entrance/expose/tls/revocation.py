"""Build the Hive's certificate revocation list over every device certificate taken back.

Revoking a device (ADR-0041) must shut the mutual-TLS gate on it as well as ending its sessions:
the remote listener's TLS context checks every client certificate against a revocation list
signed by the Hive's own authority (``VERIFY_CRL_CHECK_LEAF``), and a revocation rebuilds the list
and the context (``hivemind.entrance.expose.tls.switch``). ``build_crl`` is that list. It never
leaves the Hive Stand: it is loaded into the listener's own context and rebuilt from the Entrance's
records on every revocation and every start, so its freshness comes from the rebuild, not from
``nextUpdate``, which is therefore set to the authority's own end (a short one would make every
handshake fail the moment it passed, revoked or not).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.expose.tls``. Called by
    the Entrance's composition root at start and by the revocation path, with the serials of
    every revoked device certificate. Calls into ``cryptography`` and ``HiveAuthority``.

Key invariants:
    - Every serial given appears exactly once, with the earliest revocation time given for it.
    - The list is always signed by the authority and names it (issuer and key identifier), and
      an empty list is still a list: under ``VERIFY_CRL_CHECK_LEAF`` OpenSSL refuses every
      certificate when no list for its issuer is loaded at all.

See Also:
    - RFC 5280 section 5 for the list's shape.
    - hivemind.entrance.expose.tls.context for where the list is loaded.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from cryptography import x509

from hivemind.entrance.expose.tls.authority import NOT_BEFORE_SKEW, HiveAuthority

_MICROSECONDS = 1_000_000  # The CRL number counts microseconds, so rebuilds always increase it.

__all__ = ["RevokedSerial", "build_crl"]


@dataclass(frozen=True, slots=True)
class RevokedSerial:
    """One revoked device certificate.

    Attributes:
        serial: The certificate's serial number, as ``DeviceCertificate.serial`` recorded it.
        revoked_at: When the device was revoked.
    """

    serial: int
    revoked_at: datetime


def build_crl(
    authority: HiveAuthority, revoked: Iterable[RevokedSerial], now: datetime
) -> x509.CertificateRevocationList:
    """Sign a revocation list naming every revoked device certificate.

    Args:
        authority: The Hive's certificate authority, which issued every certificate listed.
        revoked: Every revoked certificate, in any order; duplicates are merged.
        now: The time the list is built; its ``lastUpdate`` is just before it.

    Returns:
        The signed list, empty when nothing is revoked, valid until the authority expires.
    """
    earliest: dict[int, datetime] = {}
    # One entry per serial, keeping the first revocation: a device is revoked once.
    for entry in revoked:
        previous = earliest.get(entry.serial)
        if previous is None or entry.revoked_at < previous:
            earliest[entry.serial] = entry.revoked_at
    builder = (
        x509.CertificateRevocationListBuilder()
        .issuer_name(authority.subject)
        .last_update(now - NOT_BEFORE_SKEW)
        .next_update(authority.not_after)
        .add_extension(authority.key_identifier, critical=False)
        .add_extension(x509.CRLNumber(int(now.timestamp() * _MICROSECONDS)), critical=False)
    )
    # Sorted for a stable list: the same revocations always produce the same entries.
    for serial in sorted(earliest):
        builder = builder.add_revoked_certificate(
            x509.RevokedCertificateBuilder()
            .serial_number(serial)
            .revocation_date(earliest[serial])
            .build()
        )
    return authority.sign_revocation_list(builder)
