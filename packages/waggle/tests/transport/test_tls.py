"""Tests for waggle.transport.tls: the server context, the pin-only client context and the pin form.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises the pure and file-reading parts of
    tls.py without a network: the fingerprint of a certificate against a known SHA-256 vector, the
    exact spelling a pin must have, a server context built from a generated self-signed
    certificate (and its refusals), and the pin-only client context's settings. The check against
    a live TLS connection is test_websocket_tls.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.tls for the module under test.
    - conftest.py for the generated certificate fixtures.
"""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from waggle.transport.tls import (
    PIN_HEX_CHARS,
    certificate_sha256,
    is_valid_pin,
    pinned_client_ssl_context,
    server_ssl_context,
)

if TYPE_CHECKING:
    # mypy resolves the conftest by its path-based module name (explicit_package_bases); the
    # annotations below are never evaluated at run time (from __future__ import annotations).
    from packages.waggle.tests.transport.conftest import TlsMaterial

ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"  # FIPS 180-2.


def test_certificate_sha256_is_the_lowercase_hex_of_the_digest() -> None:
    # The standard SHA-256 test vector for "abc": the function adds nothing and drops nothing.
    assert certificate_sha256(b"abc") == ABC_SHA256


def test_the_fingerprint_of_a_generated_certificate_is_a_valid_pin(
    tls_material: TlsMaterial,
) -> None:
    assert is_valid_pin(tls_material.sha256)
    assert len(tls_material.sha256) == PIN_HEX_CHARS


@pytest.mark.parametrize(
    ("pin", "valid"),
    [
        (ABC_SHA256, True),
        ("0" * 64, True),
        (ABC_SHA256.upper(), False),  # uppercase is another spelling of the same digest
        ("ba:78:16:bf:8f:01:cf:ea:41:41:40:de:5d:ae:22:23", False),  # colon-separated
        (ABC_SHA256[:-1], False),  # one character short
        (ABC_SHA256 + "0", False),  # one character long
        ("g" * 64, False),  # not hexadecimal
        (f" {ABC_SHA256}", False),  # whitespace is not tolerated
        (f"{ABC_SHA256}\n", False),  # nor a trailing newline, which `$` would let through
        ("", False),
    ],
)
def test_a_pin_is_exactly_sixty_four_lowercase_hex_characters(pin: str, valid: bool) -> None:
    assert is_valid_pin(pin) is valid


def test_server_ssl_context_serves_the_certificate_at_tls_1_2_or_later(
    tls_material: TlsMaterial,
) -> None:
    context = server_ssl_context(tls_material.cert_path, tls_material.key_path)

    assert context.protocol == ssl.PROTOCOL_TLS_SERVER
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    # It asks clients for no certificate: a node proves itself with Waggle signatures.
    assert context.verify_mode == ssl.CERT_NONE


def test_server_ssl_context_accepts_paths_as_text(tls_material: TlsMaterial) -> None:
    context = server_ssl_context(str(tls_material.cert_path), str(tls_material.key_path))

    assert context.protocol == ssl.PROTOCOL_TLS_SERVER


def test_server_ssl_context_refuses_a_missing_file(
    tmp_path: Path, tls_material: TlsMaterial
) -> None:
    with pytest.raises(FileNotFoundError):
        server_ssl_context(tmp_path / "absent.crt", tls_material.key_path)


def test_server_ssl_context_refuses_a_key_that_does_not_match_the_certificate(
    tls_material: TlsMaterial, other_tls_material: TlsMaterial
) -> None:
    with pytest.raises(ssl.SSLError):
        server_ssl_context(tls_material.cert_path, other_tls_material.key_path)


def test_the_pinned_client_context_trusts_no_authority_and_checks_no_host_name() -> None:
    context = pinned_client_ssl_context()

    assert context.protocol == ssl.PROTOCOL_TLS_CLIENT
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    # No certificate authority is loaded: the pin, not a chain, is what makes a peer acceptable.
    assert context.get_ca_certs() == []
