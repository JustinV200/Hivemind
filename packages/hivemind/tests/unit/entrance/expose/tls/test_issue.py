"""Tests for hivemind.entrance.expose.tls.issue: device certificates from a CSR or as PKCS#12.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/tls/issue.py (codingrules section 3). Every request is
    signed by a real key, the way a program signs one with its own device key.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.tls.issue for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa
from cryptography.hazmat.primitives.asymmetric.types import CertificatePublicKeyTypes
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import SecretStr
from unit.entrance.expose.support import DEVICE_ID, HIVE_ID, START, make_csr

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.expose import (
    CertificateAuthorityError,
    CertificateIssueError,
    CertificateRequestError,
)
from hivemind.entrance.expose.tls import (
    CA_VALIDITY,
    CLIENT_CERT_VALIDITY,
    MAX_CSR_BYTES,
    NOT_BEFORE_SKEW,
    HiveAuthority,
    issue_client_certificate,
    issue_pkcs12,
    load_or_create_authority,
)
from waggle.ids import DeviceId

_PASSPHRASE = SecretStr("correct-horse-battery-staple")  # A generated-looking bundle passphrase.


@pytest.fixture
async def authority() -> HiveAuthority:
    """A fresh authority minted at START."""
    return await load_or_create_authority(MemorySecretStore(), HIVE_ID, START)


def _leaf(pem: bytes) -> x509.Certificate:
    """Parse an issued certificate."""
    return x509.load_pem_x509_certificate(pem)


def _spki(key: CertificatePublicKeyTypes) -> bytes:
    """A public key in one comparable encoding."""
    return key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def test_a_program_key_csr_gets_a_device_certificate_on_the_hives_terms(
    authority: HiveAuthority,
) -> None:
    # A program's Ed25519 device key asks, in its request, to be an authority named "whatever".
    program_key = ed25519.Ed25519PrivateKey.generate()

    issued = issue_client_certificate(authority, make_csr(program_key), DEVICE_ID, START)

    leaf = _leaf(issued.pem)
    leaf.verify_directly_issued_by(authority.certificate)
    assert leaf.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == DEVICE_ID
    assert _spki(program_key.public_key()) == _spki(leaf.public_key())
    constraints = leaf.extensions.get_extension_for_class(x509.BasicConstraints)
    assert (constraints.value.ca, constraints.critical) == (False, True)
    usage = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert list(usage) == [ExtendedKeyUsageOID.CLIENT_AUTH]
    assert leaf.extensions.get_extension_for_class(x509.KeyUsage).value.digital_signature
    assert leaf.not_valid_before_utc == START - NOT_BEFORE_SKEW
    assert leaf.not_valid_after_utc == START + CLIENT_CERT_VALIDITY
    assert (issued.device_id, issued.serial, issued.not_after) == (
        DEVICE_ID,
        leaf.serial_number,
        leaf.not_valid_after_utc,
    )


@pytest.mark.parametrize(
    "key",
    [
        ec.generate_private_key(ec.SECP256R1()),
        ec.generate_private_key(ec.SECP384R1()),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
    ],
    ids=["p256", "p384", "rsa2048"],
)
def test_the_accepted_key_kinds_are_certified(
    authority: HiveAuthority, key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey
) -> None:
    issued = issue_client_certificate(authority, make_csr(key), DEVICE_ID, START)

    assert _spki(_leaf(issued.pem).public_key()) == _spki(key.public_key())


@pytest.mark.parametrize(
    "key",
    [
        ec.generate_private_key(ec.SECP521R1()),
        rsa.generate_private_key(65537, 1024),  # noqa: S505 -- weak on purpose: must be refused
        ed448.Ed448PrivateKey.generate(),
    ],
    ids=["p521", "rsa1024", "ed448"],
)
def test_other_key_kinds_and_sizes_are_refused(
    authority: HiveAuthority,
    key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey | ed448.Ed448PrivateKey,
) -> None:
    with pytest.raises(CertificateRequestError, match="its key must be"):
        issue_client_certificate(authority, make_csr(key), DEVICE_ID, START)


def test_a_request_whose_signature_does_not_verify_is_refused(authority: HiveAuthority) -> None:
    # Flip one bit inside the signature at the end of the DER request.
    request = x509.load_pem_x509_csr(make_csr(ec.generate_private_key(ec.SECP256R1())))
    der = bytearray(request.public_bytes(serialization.Encoding.DER))
    der[-3] ^= 0x01
    tampered = x509.load_der_x509_csr(bytes(der)).public_bytes(serialization.Encoding.PEM)

    with pytest.raises(CertificateRequestError, match="signature does not verify"):
        issue_client_certificate(authority, tampered, DEVICE_ID, START)


@pytest.mark.parametrize(
    ("request_bytes", "message"),
    [
        (b"not a request", "not a PEM certificate request"),
        (b"-" * (MAX_CSR_BYTES + 1), "larger than"),
    ],
)
def test_malformed_requests_are_refused_without_echoing_them(
    authority: HiveAuthority, request_bytes: bytes, message: str
) -> None:
    with pytest.raises(CertificateRequestError, match=message) as caught:
        issue_client_certificate(authority, request_bytes, DEVICE_ID, START)

    assert request_bytes.decode() not in str(caught.value)


def test_a_malformed_device_id_is_refused(authority: HiveAuthority) -> None:
    key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(CertificateIssueError, match="not a device id"):
        issue_client_certificate(authority, make_csr(key), DeviceId("CN=admin"), START)


def test_an_expired_authority_issues_nothing(authority: HiveAuthority) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    after_the_end = START + CA_VALIDITY + timedelta(seconds=1)

    with pytest.raises(CertificateAuthorityError):
        issue_client_certificate(authority, make_csr(key), DEVICE_ID, after_the_end)
    with pytest.raises(CertificateAuthorityError):
        issue_pkcs12(authority, DEVICE_ID, after_the_end, _PASSPHRASE)


def test_no_certificate_outlives_its_authority(authority: HiveAuthority) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    near_the_end = authority.not_after - timedelta(days=10)

    issued = issue_client_certificate(authority, make_csr(key), DEVICE_ID, near_the_end)

    assert issued.not_after == authority.not_after


def test_every_certificate_gets_its_own_serial(authority: HiveAuthority) -> None:
    key = ec.generate_private_key(ec.SECP256R1())

    serials = {
        issue_client_certificate(authority, make_csr(key), DEVICE_ID, START).serial
        for _ in range(5)
    }

    assert len(serials) == 5


def test_a_browser_bundle_opens_with_its_passphrase(authority: HiveAuthority) -> None:
    bundle = issue_pkcs12(authority, DEVICE_ID, START, _PASSPHRASE)

    opened = pkcs12.load_pkcs12(bundle.pkcs12, _PASSPHRASE.get_secret_value().encode())

    assert opened.key is not None
    assert opened.cert is not None
    leaf = opened.cert.certificate
    leaf.verify_directly_issued_by(authority.certificate)
    assert isinstance(opened.key, ec.EllipticCurvePrivateKey)
    assert _spki(opened.key.public_key()) == _spki(leaf.public_key())
    # Never the authority: a phone would trust it as a root for every site.
    assert opened.additional_certs == []
    assert opened.cert.friendly_name == f"HiveMind {DEVICE_ID}".encode()
    assert bundle.certificate.serial == leaf.serial_number
    assert bundle.certificate.pem == leaf.public_bytes(serialization.Encoding.PEM)


def test_a_browser_bundle_does_not_open_without_its_passphrase(authority: HiveAuthority) -> None:
    bundle = issue_pkcs12(authority, DEVICE_ID, START, _PASSPHRASE)

    with pytest.raises(ValueError):
        pkcs12.load_pkcs12(bundle.pkcs12, b"a wrong passphrase entirely")


def test_every_bundle_holds_a_fresh_key(authority: HiveAuthority) -> None:
    first = issue_pkcs12(authority, DEVICE_ID, START, _PASSPHRASE)
    second = issue_pkcs12(authority, DEVICE_ID, START, _PASSPHRASE)

    assert first.certificate.pem != second.certificate.pem


def test_a_short_passphrase_is_refused_without_being_echoed(authority: HiveAuthority) -> None:
    with pytest.raises(CertificateIssueError, match="at least") as caught:
        issue_pkcs12(authority, DEVICE_ID, START, SecretStr("hunter2"))

    assert "hunter2" not in str(caught.value)


def test_a_bundle_never_shows_its_bytes(authority: HiveAuthority) -> None:
    bundle = issue_pkcs12(authority, DEVICE_ID, START, _PASSPHRASE)

    assert "pkcs12" not in repr(bundle)
    assert _PASSPHRASE.get_secret_value() not in repr(bundle)
