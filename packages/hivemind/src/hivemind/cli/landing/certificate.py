"""Hold a device's mutual-TLS client certificate: ask for one, check it, present it on every call.

In ``lan`` and ``tunnel`` modes (and ``vpn`` when the operator turns mutual TLS on) the remote
listener completes a handshake only with a client certificate from the Hive's own authority
(ADR-0033), and login is the gate behind it. A CLI device's certificate certifies the same Ed25519
key it signs every request with, so there is one key to keep: ``certificate_request`` builds the
PKCS#10 request for that key (its subject is the device's name, which the Hive ignores; the
request's signature is what proves the key), and the Hive signs the certificate at approval.
``certificate_facts`` reads a certificate back, however it arrived (fetched over the listener the
device enrolled on, or carried by the operator for a device registered offline), and refuses one
that does not certify this device's key, has expired, or names no device. ``ClientCertificate`` is
the certificate with its key, and ``present`` loads both into a TLS context: the standard library
reads a key only from a file, so the key goes to a private temporary directory sealed under a
one-time password that lives only in memory, and the directory is gone before ``present``
returns.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Used by ``transport`` (every HTTPS
    request and WebSocket of a device holding a certificate) and by ``hivemind.cli.remote``
    (enrolment's request, the certificate commands). Calls into ``cryptography``, ``ssl`` and
    ``waggle.signing``.

Key invariants:
    - The private key never touches a disk unencrypted and is never printed or logged; the
      one-time password sealing its temporary copy exists only in this process's memory.
    - A certificate is accepted only for the key this device holds.

See Also:
    - hivemind.entrance.expose.tls.issue for how the Hive signs the request.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "lan and tunnel".
"""

from __future__ import annotations

import os
import ssl
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

from hivemind.cli.landing.errors import LandingError
from waggle.errors import InvalidIdError
from waggle.ids import IdKind, parse_id
from waggle.signing import Ed25519Signer

MAX_SUBJECT_CHARS = 64  # X.509's upper bound on a common name; a device name fits.
_ONE_TIME_PASSWORD_BYTES = 32  # Seals the key's temporary copy; never leaves this process.
_CERT_FILE = "device.crt"  # The certificate's temporary copy, for load_cert_chain.
_KEY_FILE = "device.key"  # The key's sealed temporary copy, beside it.

__all__ = [
    "CertificateFacts",
    "ClientCertificate",
    "certificate_facts",
    "certificate_request",
    "present",
]


@dataclass(frozen=True, slots=True)
class ClientCertificate:
    """A device's client certificate and the key it certifies.

    Attributes:
        certificate_pem: The certificate, PEM (public).
        signer: The device's own key; never printed (left out of the ``repr``).
    """

    certificate_pem: bytes
    signer: Ed25519Signer = field(repr=False)


@dataclass(frozen=True, slots=True)
class CertificateFacts:
    """What a certificate says, once it was checked against this device's key.

    Attributes:
        device_id: The device it names (its subject's common name).
        serial: Its serial number, lowercase hex, as the Hive records it.
        fingerprint: SHA-256 of the certificate, lowercase hex, as the Hive Stand shows it.
        not_after: When it expires.
    """

    device_id: str
    serial: str
    fingerprint: str
    not_after: datetime


def certificate_request(signer: Ed25519Signer, name: str) -> str:
    """Build a certificate signing request for the device's own key.

    Args:
        signer: The device's key; the request is signed with it, which proves the key.
        name: What the device calls itself; the subject, which the Hive replaces anyway.

    Returns:
        The request, PEM.
    """
    key = Ed25519PrivateKey.from_private_bytes(signer.private_key_bytes)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name[:MAX_SUBJECT_CHARS])])
    # Ed25519 signs its own digest: the algorithm argument is None by the library's contract.
    request = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, None)
    return request.public_bytes(serialization.Encoding.PEM).decode("ascii")


def certificate_facts(pem: bytes, signer: Ed25519Signer, now: datetime) -> CertificateFacts:
    """Read a client certificate and check it is this device's, current, and names a device.

    Args:
        pem: The certificate, PEM, however it arrived.
        signer: The device's key; the certificate must certify its public half.
        now: The time it must still be valid at.

    Returns:
        What it says.

    Raises:
        LandingError: It does not parse, certifies another key, has expired, or names no device.
    """
    try:
        certificate = x509.load_pem_x509_certificate(pem)
    except ValueError as exc:
        raise LandingError("That file is not a PEM certificate.") from exc
    public = certificate.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    # The certificate is only worth keeping if it certifies the key this device signs with.
    if public != signer.public_key_bytes:
        raise LandingError("That certificate is for another key than this device's.")
    if certificate.not_valid_after_utc <= now.astimezone(UTC):
        raise LandingError("That certificate has expired; the device enrols again for another.")
    return CertificateFacts(
        device_id=_device_named(certificate),
        serial=format(certificate.serial_number, "x"),
        fingerprint=certificate.fingerprint(hashes.SHA256()).hex(),
        not_after=certificate.not_valid_after_utc,
    )


def present(context: ssl.SSLContext, client: ClientCertificate) -> None:
    """Make ``context`` present the certificate, with its key, on every handshake.

    Args:
        context: A client context (``transport``'s, before any connection uses it).
        client: The certificate and the key it certifies.

    Raises:
        LandingError: OpenSSL refused the pair (the certificate does not match the key).
    """
    password = os.urandom(_ONE_TIME_PASSWORD_BYTES)
    key = Ed25519PrivateKey.from_private_bytes(client.signer.private_key_bytes)
    sealed = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password),
    )
    # load_cert_chain reads files only: both go to a private directory, gone before this returns.
    with tempfile.TemporaryDirectory(prefix="hive-client-") as directory:
        cert_path, key_path = Path(directory) / _CERT_FILE, Path(directory) / _KEY_FILE
        cert_path.write_bytes(client.certificate_pem)
        key_path.write_bytes(sealed)
        try:
            context.load_cert_chain(cert_path, key_path, password=password)
        except ssl.SSLError as exc:
            raise LandingError(
                "The client certificate kept for this profile does not match its key; fetch or "
                "import it again."
            ) from exc


def _device_named(certificate: x509.Certificate) -> str:
    """The device id a certificate names in its common name, or refuse it."""
    names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    value = names[0].value if names else ""
    try:
        return parse_id(str(value), IdKind.DEVICE)
    except InvalidIdError as exc:
        raise LandingError("That certificate names no device of a Hive.") from exc
