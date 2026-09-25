"""Test hivemind.cli.landing.transport: which Entrance URLs are accepted, and how TLS is verified.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/transport.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from hivemind.cli.landing import LandingError, entrance_address, open_http


def _authority_pem(path: Path) -> Path:
    """Write a self-signed CA certificate, as a Hive's own authority would be, and return it."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Hive authority")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return path


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://hive.example.ts.net:8711/enrol#code=ABCD", "https://hive.example.ts.net:8711"),
        ("http://localhost:8710", "http://localhost:8710"),
        ("http://127.0.0.1:8710/", "http://127.0.0.1:8710"),
        ("http://[::1]:8710", "http://[::1]:8710"),
    ],
)
def test_an_entrance_url_keeps_only_its_origin(url: str, origin: str) -> None:
    assert entrance_address(url).origin == origin


@pytest.mark.parametrize(
    "url",
    [
        "http://hive.example.ts.net:8711",  # Plain http off the machine.
        "http://100.64.0.7:8711",  # An overlay address is still a network.
        "ftp://localhost/",
        "https://",
        "hive.example.ts.net",
    ],
)
def test_a_url_that_is_not_https_or_loopback_http_is_refused(url: str) -> None:
    with pytest.raises(LandingError):
        entrance_address(url)


def test_a_views_origin_uses_the_websocket_scheme_of_its_own() -> None:
    assert entrance_address("https://hive.example.ts.net").socket_origin == (
        "wss://hive.example.ts.net"
    )
    assert entrance_address("http://localhost:8710").socket_origin == "ws://localhost:8710"


def test_loopback_http_needs_no_tls_and_https_always_verifies() -> None:
    plain = entrance_address("http://localhost:8710").ssl_context()
    secure = entrance_address("https://hive.example.ts.net").ssl_context()

    assert plain is None
    assert secure is not None
    assert secure.verify_mode is ssl.CERT_REQUIRED and secure.check_hostname


def test_a_named_ca_file_is_the_only_authority_trusted(tmp_path: Path) -> None:
    ca_file = _authority_pem(tmp_path / "hive-ca.pem")

    context = entrance_address("https://hive.example.ts.net", ca_file).ssl_context()

    assert context is not None
    assert [entry["subject"] for entry in context.get_ca_certs()] == [
        ((("commonName", "Hive authority"),),)
    ]


def test_a_ca_file_that_is_not_pem_is_refused_in_a_sentence(tmp_path: Path) -> None:
    ca_file = tmp_path / "not-a-certificate.pem"
    ca_file.write_text("hello", encoding="utf-8")

    with pytest.raises(LandingError, match="could not be read as PEM"):
        entrance_address("https://hive.example.ts.net", ca_file).ssl_context()


async def test_open_http_points_its_client_at_the_origin() -> None:
    async with open_http(entrance_address("http://localhost:8710/enrol")) as http:
        assert str(http.base_url) == "http://localhost:8710"
