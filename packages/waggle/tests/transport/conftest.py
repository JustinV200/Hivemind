"""Fixtures the transport tests share: throwaway TLS certificates and a refusal recorder.

The TLS pinning tests (test_tls.py, test_websocket_tls.py) need a real certificate and key on
disk, because ``server_ssl_context`` reads files, and a second, different certificate to prove a
pin refuses the wrong one. They are generated here with ``cryptography`` (already a waggle
dependency) rather than committed, so no private key ever lives in the repository and a
certificate never expires under a test. The limits tests (test_limits.py,
test_websocket_server_limits.py) share one more fixture pair: a list and the ``on_refusal``
callback that appends every ConnectionRefusal to it. Only transport tests see these fixtures; the
waggle-wide fixtures stay in the parent conftest.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Loaded automatically by pytest for every test
    module in this directory. Depends on cryptography and waggle.transport.tls; nothing but the
    TLS tests depends on it.

Key invariants:
    - A certificate is valid from yesterday to tomorrow, covers ``localhost`` and 127.0.0.1, and
      is self-signed: nothing trusts it, so only a pin can accept it.
    - Each fixture call makes a fresh key, so two materials never share a fingerprint.

See Also:
    - waggle.transport.tls for the functions the fixtures feed.
    - ../conftest.py for the waggle-wide composition root.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from cryptography.x509.oid import NameOID

from waggle.transport.limits import ConnectionRefusal, RefusalHandler
from waggle.transport.tls import certificate_sha256

VALIDITY_MARGIN = timedelta(days=1)  # Wide enough that no test run straddles an expiry.


@dataclass(frozen=True, slots=True)
class TlsMaterial:
    """A self-signed certificate and its key on disk, with the pin that names the certificate.

    Attributes:
        cert_path: The PEM certificate, as ``server_ssl_context`` reads it.
        key_path: The PEM private key (PKCS#8, unencrypted).
        sha256: The pin: the SHA-256 of the certificate's DER bytes, lowercase hex.
    """

    cert_path: Path
    key_path: Path
    sha256: str


def make_tls_material(directory: Path, name: str) -> TlsMaterial:
    """Write a fresh self-signed certificate and key named ``name`` into ``directory``."""
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


@pytest.fixture
def tls_material(tmp_path: Path) -> TlsMaterial:
    """A fresh self-signed certificate for the listener under test."""
    return make_tls_material(tmp_path, "listener")


@pytest.fixture
def other_tls_material(tmp_path: Path) -> TlsMaterial:
    """A second, different certificate: what an impostor presents, or a rotated listener."""
    return make_tls_material(tmp_path, "impostor")


@pytest.fixture
def refusals() -> list[ConnectionRefusal]:
    """Every refusal the ``on_refusal`` fixture has been handed, oldest first."""
    return []


@pytest.fixture
def on_refusal(refusals: list[ConnectionRefusal]) -> RefusalHandler:
    """A listener's ``on_refusal`` callback that records each refusal into ``refusals``."""

    async def record(refusal: ConnectionRefusal) -> None:
        """Append ``refusal`` to the shared list."""
        refusals.append(refusal)

    return record
