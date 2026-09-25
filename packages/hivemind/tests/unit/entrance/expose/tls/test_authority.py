"""Tests for hivemind.entrance.expose.tls.authority: the Hive's own certificate authority.

Fits into the Hive:
    Mirrors src/hivemind/entrance/expose/tls/authority.py (codingrules section 3). The secret
    store is the in-memory one; the key material is real P-256.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.expose.tls.authority for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from unit.entrance.expose.support import DEVICE_ID, HIVE_ID, START, make_csr

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.expose import CertificateAuthorityError
from hivemind.entrance.expose.tls import (
    CA_CERT_NAME,
    CA_KEY_NAME,
    CA_VALIDITY,
    NOT_BEFORE_SKEW,
    HiveAuthority,
    issue_client_certificate,
    load_or_create_authority,
)


async def test_first_use_mints_a_p256_authority_and_stores_both_halves() -> None:
    store = MemorySecretStore()

    authority = await load_or_create_authority(store, HIVE_ID, START)

    assert await store.names() == (CA_CERT_NAME, CA_KEY_NAME)
    stored_key = serialization.load_der_private_key(await store.get(CA_KEY_NAME) or b"", None)
    assert isinstance(stored_key, ec.EllipticCurvePrivateKey)
    assert isinstance(stored_key.curve, ec.SECP256R1)
    assert await store.get(CA_CERT_NAME) == authority.certificate_pem


async def test_the_certificate_is_a_self_signed_leaf_only_authority() -> None:
    authority = await load_or_create_authority(MemorySecretStore(), HIVE_ID, START)
    certificate = authority.certificate

    constraints = certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    usage = certificate.extensions.get_extension_for_class(x509.KeyUsage)
    assert constraints.critical is True
    assert (constraints.value.ca, constraints.value.path_length) == (True, 0)
    assert usage.critical is True
    assert (usage.value.key_cert_sign, usage.value.crl_sign) == (True, True)
    assert usage.value.digital_signature is False
    certificate.verify_directly_issued_by(certificate)
    common_name = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    assert HIVE_ID in str(common_name)
    assert certificate.not_valid_before_utc == START - NOT_BEFORE_SKEW
    assert authority.not_after == START + CA_VALIDITY


async def test_later_starts_load_the_same_authority() -> None:
    store = MemorySecretStore()
    first = await load_or_create_authority(store, HIVE_ID, START)

    again = await load_or_create_authority(store, HIVE_ID, START + timedelta(days=400))

    assert again.certificate == first.certificate


async def test_a_lost_certificate_is_rederived_so_old_device_certificates_still_chain() -> None:
    store = MemorySecretStore()
    first = await load_or_create_authority(store, HIVE_ID, START)
    device_key = ec.generate_private_key(ec.SECP256R1())
    issued = issue_client_certificate(first, make_csr(device_key), DEVICE_ID, START)
    await store.delete(CA_CERT_NAME)

    rederived = await load_or_create_authority(store, HIVE_ID, START + timedelta(days=1))

    assert rederived.certificate != first.certificate
    assert await store.get(CA_CERT_NAME) == rederived.certificate_pem
    x509.load_pem_x509_certificate(issued.pem).verify_directly_issued_by(rederived.certificate)


async def test_a_certificate_without_its_key_is_refused_never_replaced() -> None:
    store = MemorySecretStore()
    await load_or_create_authority(store, HIVE_ID, START)
    stored_cert = await store.get(CA_CERT_NAME)
    await store.delete(CA_KEY_NAME)

    with pytest.raises(CertificateAuthorityError, match=CA_KEY_NAME):
        await load_or_create_authority(store, HIVE_ID, START)

    assert await store.get(CA_CERT_NAME) == stored_cert
    assert await store.get(CA_KEY_NAME) is None


async def test_a_key_and_a_certificate_that_do_not_belong_together_are_refused() -> None:
    store = MemorySecretStore()
    await load_or_create_authority(store, HIVE_ID, START)
    other = MemorySecretStore()
    await load_or_create_authority(other, HIVE_ID, START)
    await store.put(CA_CERT_NAME, await other.get(CA_CERT_NAME) or b"")

    with pytest.raises(CertificateAuthorityError, match="is not the certificate of"):
        await load_or_create_authority(store, HIVE_ID, START)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (CA_KEY_NAME, b"not a key", "not an unencrypted PKCS#8"),
        (CA_CERT_NAME, b"not a certificate", "not a PEM certificate"),
    ],
)
async def test_a_mangled_half_is_refused_by_name(name: str, value: bytes, message: str) -> None:
    store = MemorySecretStore()
    await load_or_create_authority(store, HIVE_ID, START)
    await store.put(name, value)

    with pytest.raises(CertificateAuthorityError, match=message):
        await load_or_create_authority(store, HIVE_ID, START)


async def test_a_key_on_another_curve_is_refused() -> None:
    store = MemorySecretStore()
    wrong = ec.generate_private_key(ec.SECP384R1()).private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    await store.put(CA_KEY_NAME, wrong)

    with pytest.raises(CertificateAuthorityError, match="not a P-256 key"):
        await load_or_create_authority(store, HIVE_ID, START)


def _hand_made(
    key: ec.EllipticCurvePrivateKey,
    constraints: x509.BasicConstraints | None,
    signer: ec.EllipticCurvePrivateKey | None = None,
) -> x509.Certificate:
    """A self-issued certificate for ``key``, without a key identifier, signed by ``signer``."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hand made")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(START)
        .not_valid_after(START + timedelta(days=1))
    )
    if constraints is not None:
        builder = builder.add_extension(constraints, critical=True)
    return builder.sign(signer if signer is not None else key, hashes.SHA256())


@pytest.mark.parametrize(
    "constraints", [x509.BasicConstraints(ca=False, path_length=None), None], ids=["leaf", "none"]
)
def test_a_certificate_that_is_not_an_authority_is_refused(
    constraints: x509.BasicConstraints | None,
) -> None:
    key = ec.generate_private_key(ec.SECP256R1())

    with pytest.raises(CertificateAuthorityError, match="not a certificate authority"):
        HiveAuthority(key, _hand_made(key, constraints))


def test_a_certificate_whose_self_signature_does_not_verify_is_refused() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    forged = _hand_made(
        key, x509.BasicConstraints(ca=True, path_length=0), ec.generate_private_key(ec.SECP256R1())
    )

    with pytest.raises(CertificateAuthorityError, match="self-signature"):
        HiveAuthority(key, forged)


def test_an_authority_without_a_subject_key_id_still_names_its_key() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    authority = HiveAuthority(key, _hand_made(key, x509.BasicConstraints(ca=True, path_length=0)))

    expected = x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key())
    assert authority.key_identifier == expected


async def test_an_expired_authority_is_refused_on_load() -> None:
    store = MemorySecretStore()
    await load_or_create_authority(store, HIVE_ID, START)

    with pytest.raises(CertificateAuthorityError, match="re-issue every device"):
        await load_or_create_authority(store, HIVE_ID, START + CA_VALIDITY + timedelta(days=1))


async def test_an_authority_from_the_future_is_refused_on_load() -> None:
    store = MemorySecretStore()
    await load_or_create_authority(store, HIVE_ID, START)

    with pytest.raises(CertificateAuthorityError, match="is valid from"):
        await load_or_create_authority(store, HIVE_ID, START - timedelta(days=1))


async def test_repr_names_the_authority_and_never_its_key() -> None:
    store = MemorySecretStore()
    authority = await load_or_create_authority(store, HIVE_ID, START)
    key_bytes = await store.get(CA_KEY_NAME) or b""

    shown = repr(authority)

    assert HIVE_ID in shown
    assert key_bytes.hex() not in shown
    assert not hasattr(authority, "private_key")
