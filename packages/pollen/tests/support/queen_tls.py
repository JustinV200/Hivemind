"""Make throwaway TLS certificates for the pollen tests that dial a pinned ``wss://`` Queen.

The Pollen Packet (the thin gateway program on a device the Hive borrows) trusts no certificate
authority: it compares the SHA-256 of the DER bytes of the certificate the Queen presents with the
fingerprint pinned in its invite. The tests that prove this need a real certificate and key on
disk (``server_ssl_context`` reads files) and a second, different one to prove a wrong
certificate is refused. They are generated here with ``cryptography``, which waggle already
depends on, rather than committed, so no private key ever lives in the repository and no
certificate can expire under a test.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Imported by queen_double.py and by the tests that
    need a second certificate; depends on cryptography and waggle.transport.tls.

Key invariants:
    - A certificate is valid from yesterday to tomorrow, covers ``localhost`` and 127.0.0.1, and
      is self-signed: nothing trusts it, so only a pin can accept it.
    - Each call makes a fresh key, so two materials never share a fingerprint.

See Also:
    - waggle.transport.tls for the functions these files feed.
    - queen_double.py for the listener that serves them.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from waggle.transport.tls import certificate_sha256

VALIDITY_MARGIN = timedelta(days=1)  # Wide enough that no test run straddles an expiry.

__all__ = ["TlsMaterial", "make_tls_material"]


@dataclass(frozen=True, slots=True)
class TlsMaterial:
    """A self-signed certificate and its key on disk, with the pin that names the certificate."""

    cert_path: Path  # The PEM certificate, as ``server_ssl_context`` reads it.
    key_path: Path  # The PEM private key (PKCS#8, unencrypted).
    sha256: str  # The pin: SHA-256 of the certificate's DER bytes, lowercase hex.


def make_tls_material(directory: Path, name: str) -> TlsMaterial:
    """Write a fresh self-signed certificate and key named ``name`` into ``directory``.

    Args:
        directory: Where the two files go.
        name: The file stem and the certificate's common name.

    Returns:
        The files and the pin that names the certificate.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)  # Real time on purpose: OpenSSL validates a certificate against it.
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - VALIDITY_MARGIN)
        .not_valid_after(now + VALIDITY_MARGIN)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / f"{name}.crt"
    key_path = directory / f"{name}.key"
    cert_path.write_bytes(certificate.public_bytes(Encoding.PEM))
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return TlsMaterial(
        cert_path=cert_path,
        key_path=key_path,
        sha256=certificate_sha256(certificate.public_bytes(Encoding.DER)),
    )
