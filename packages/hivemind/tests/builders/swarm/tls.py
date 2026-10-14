"""Write a throwaway self-signed certificate for the swarm listener's TLS tests.

A device pins the listener's certificate by SHA-256 fingerprint instead of trusting an authority
(docs/waggle/enrolment.md section 9), so the listener tests need a real certificate and key on
disk (the TLS context reads files) and a second, different certificate to show that a pin refuses
the wrong one. They are generated here with `cryptography` for each test rather than committed, so
no private key ever lives in the repository and no certificate can expire under a test.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the listener tests under
    tests/unit/swarm/gateway.

Key invariants:
    - A certificate is valid from yesterday to tomorrow and covers `localhost`, 127.0.0.1 and
      any further host names the caller names.
    - Each call makes a fresh key, so two materials never share a fingerprint.

See Also:
    - waggle.transport.tls for the fingerprint and the contexts built from these files.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from waggle.transport.tls import certificate_sha256

__all__ = ["TlsMaterial", "make_tls_material"]

_VALIDITY_MARGIN = timedelta(days=1)  # Wide enough that no test run straddles an expiry.


@dataclass(frozen=True, slots=True)
class TlsMaterial:
    """A self-signed certificate and its key on disk, with the pin that names the certificate.

    Attributes:
        cert_path: The PEM certificate.
        key_path: The PEM private key (PKCS#8, unencrypted).
        sha256: The pin: the SHA-256 of the certificate's DER bytes, lowercase hex.
    """

    cert_path: Path
    key_path: Path
    sha256: str


def make_tls_material(directory: Path, name: str, hosts: Sequence[str] = ()) -> TlsMaterial:
    """Write a fresh self-signed certificate and key named `name` into `directory`.

    Args:
        directory: Where the `.crt` and `.key` files are written.
        name: The certificate's common name and the files' stem.
        hosts: Host names the certificate also covers, for a device that dials the listener
            by a name of its own (a container's `--add-host`) rather than by `localhost`.

    Returns:
        The two paths and the certificate's pin.
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
        .not_valid_before(now - _VALIDITY_MARGIN)
        .not_valid_after(now + _VALIDITY_MARGIN)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    *(x509.DNSName(host) for host in hosts),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / f"{name}.crt", directory / f"{name}.key"
    cert_path.write_bytes(certificate.public_bytes(Encoding.PEM))
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return TlsMaterial(
        cert_path, key_path, certificate_sha256(certificate.public_bytes(Encoding.DER))
    )
